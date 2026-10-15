"""The install orchestrator, model-free and end to end (P3, P6, P7, P9, P12,
P24, P30, P31, P33, P34).

Every child the orchestrator spawns is a stub: auth_bootstrap, warm_start,
scheduler_setup, operator_research, migrate_to_vault, scaffold, the two client
smoke tests, the notetaker validator, companion_key, keep_awake and
setup_nudge. The stubs leave the same artifacts on disk the real children
would (tokens in .env, the vault pointer, config.json, hotcache headings,
meeting pages), because every box is ticked from disk and a stub that only
returned JSON would prove nothing about the finish line. A fake clock drives
the ledger so the three time classes are measured, not guessed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import types
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import setup_flow as sf
import setup_state as ss

TZ = ZoneInfo("America/New_York")
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=TZ)
GOOGLE_TOKEN = "tok-google-SECRET-7f3a"
MS_TOKEN = "tok-ms-SECRET-19bc"
NOTETAKER_KEY = "granola-SECRET-key-55"
CLIENT_SECRET = "cs-SECRET-xyz-01"
GOOD_KEYS = {NOTETAKER_KEY}

PROPOSED_DEALS = [
    {"heading": "Harbor Point solar PPA", "name": "Dana Reyes", "email": "dana@harbor.example",
     "business": "acme", "stage": "negotiating", "last_contact": "2026-09-28",
     "next_action_due": ""},
    {"heading": "Northwind storage lease", "name": "Lee Park", "email": "lee@northwind.example",
     "business": "acme", "stage": "proposal", "last_contact": "2026-09-20",
     "next_action_due": ""},
    {"heading": "Old newsletter thread", "name": "", "email": "news@spam.example",
     "business": "acme", "stage": "cold", "last_contact": "2026-08-01", "next_action_due": ""},
]
PROPOSED_ENTITIES = [{"name": "Dana Reyes", "org": "Harbor", "threads": 3, "last_contact": "2026-09-28"},
                     {"name": "Lee Park", "org": "Northwind", "threads": 2, "last_contact": "2026-09-20"}]


class KillSwitch(RuntimeError):
    """A crash injected after a step's side effect."""


class World:
    """One temp install: state dir, home, vault, stubs and their call log."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.state = tmp_path / "state"
        self.home = tmp_path / "home"
        self.logs = tmp_path / "logs"
        for d in (self.state, self.home / "Downloads", self.logs):
            d.mkdir(parents=True)
        self.vault = self.home / sf.DEFAULT_VAULT_NAME
        monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(self.state))
        monkeypatch.setenv("VAN_GOGH_SETUP_LOGS", str(self.logs))
        # The nudge job is a stub here, so the wrapper may install and remove
        # it even though the state dir is overridden (see _nudge_job).
        monkeypatch.setenv("VAN_GOGH_NUDGE_JOB", "1")
        monkeypatch.setenv("VAN_GOGH_SCHEDULER_SYNC", "1")
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("USERPROFILE", str(self.home))
        self.clock = T0
        monkeypatch.setattr(sf, "NOW", lambda: self.clock)
        self.calls = []
        self.scan_polls_left = 2
        self.summaries_polls_left = 1
        self.jobs_loaded = set()
        self.scheduler_installs = 0
        self.cap_at = None            # "scan" | "propose" | "summaries" | "briefing"
        self.scan_error = ""          # wait-scan reports the scan died, with this reason
        self.capped_once = set()
        self.research_state = "done"
        self.ms_smoke_rc = 0
        self.auth_fail = {}           # provider -> error_class
        self.browserless = set()      # providers whose browser cannot open here
        self.no_browser_class = set() # providers that report error_class no_browser first
        self.headless_broken = False  # the by-link flow itself cannot start
        self.redirects = []           # addresses handed to --redirect-response
        self.device_completions = 0
        self.email_for = {"google": "jane@acme.example", "microsoft": "jane@corp.example"}
        self.keep_awake_calls = []
        self.bells = []
        self.nudge_calls = []
        self.detached = []
        self.key_file = self.home / "Downloads" / "van-gogh-key.json"
        self.key_file.write_text(json.dumps({
            "kind": "van-gogh-key", "version": 2, "issued": "2026-10-01",
            "credentials": {"google_client_secret": {"installed": {
                "client_id": "cid.apps", "client_secret": CLIENT_SECRET}},
                "ms_client_secret": {"client_id": "ms-cid", "tenant_id": "common"}},
            "accounts": [{"provider": "google", "email_hint": "jane@acme.example"},
                         {"provider": "microsoft", "email_hint": "jane@corp.example"}],
            "client": {"full_name": "Jane Doe", "first_name": "Jane", "company": "Acme",
                       "timezone": "America/New_York", "notetaker": "granola"},
            "support_email": "team@vangogh.example"}), encoding="utf-8")
        self._install_stubs(monkeypatch)

    # ── the stubs ────────────────────────────────────────────────────────
    def _install_stubs(self, mp):
        world = self

        def discover():
            found = [p for p in (world.home / "Downloads").glob("van-gogh-key*.json")]
            found += [p for p in (world.state / "keyfile").glob("*.json")] if (world.state / "keyfile").exists() else []
            return found

        def install_all(path):
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            creds = doc.get("credentials") or {}
            for name in ("google_client_secret", "ms_client_secret"):
                if creds.get(name):
                    (world.state / f"{name}.json").write_text(json.dumps(creds[name]), encoding="utf-8")
            dest_dir = world.state / "keyfile"
            dest_dir.mkdir(exist_ok=True)
            dest = dest_dir / "van-gogh-key.json"
            if Path(path).resolve() != dest.resolve():
                Path(path).replace(dest)
            try:
                os.chmod(dest_dir, 0o700)
                os.chmod(dest, 0o600)
            except OSError:
                pass
            return {"ok": True, "mode": "v2", "credentials": {"ok": True}, "companion": {},
                    "accounts": doc["accounts"], "client": doc["client"],
                    "support_email": doc["support_email"], "moved_to": str(dest)}

        mp.setitem(sys.modules, "companion_key",
                   types.SimpleNamespace(discover=discover, install_all=install_all))
        mp.setitem(sys.modules, "keep_awake", types.SimpleNamespace(
            start=lambda pid: world.keep_awake_calls.append(("start", pid)),
            stop=lambda: world.keep_awake_calls.append(("stop", None)),
            bell=lambda: world.bells.append(world.clock)))

        def nudge_main(argv):
            world.nudge_calls.append(list(argv))
            if argv[:1] == ["run"]:
                if ss.load() is None:
                    ss.log_event("nudge", sent=0, reason="ran, 0 to send")
                    print(json.dumps({"sent": 0, "reason": "ran, 0 to send"}))
                    return 0
                print(json.dumps({"sent": 0, "reason": "ran, 0 to send (install in progress)"}))
                return 0
            print(json.dumps({"ok": True, "state": "loaded"}))
            return 0

        mp.setitem(sys.modules, "setup_nudge", types.SimpleNamespace(main=nudge_main))

        class FakeGranola:
            @staticmethod
            def latest_id():
                if os.environ.get("GRANOLA_API_KEY") in GOOD_KEYS:
                    return "m1"
                raise RuntimeError("401 Unauthorized")

        mp.setattr(sf, "_notetaker_module", lambda provider: FakeGranola)
        mp.setattr(sf, "_run_script_raw", self.run_script)
        mp.setattr(sf, "_spawn_detached", lambda cmd, log: world.detached.append(cmd) or 4242)
        mp.setitem(ss.CHECKS, "plugin", lambda: (ss.DONE, "van-gogh 0.81.0"))
        mp.setitem(ss.CHECKS, "python", lambda: (ss.DONE, "ready"))

        def jobs_check():
            expected = {"meeting-ingest", "ingest-workspace", "job-watch", "morning-coffee",
                        "afternoon-tea", "week", "week-retro"}
            missing = expected - world.jobs_loaded
            if missing:
                return ss.OPEN, f"{len(missing)} of {len(expected)} jobs not loaded"
            return ss.DONE, f"{len(expected)} jobs loaded"

        mp.setitem(ss.CHECKS, "jobs", jobs_check)

    def tick(self, seconds):
        self.clock = self.clock + timedelta(seconds=seconds)

    # ── children ─────────────────────────────────────────────────────────
    def run_script(self, name, args, timeout=600, input_text=None, cwd=None):
        self.calls.append((name, [str(a) for a in args]))
        self.tick(2)
        args = [str(a) for a in args]
        out = {"rc": 0, "json": {}, "text": ""}

        def ret(obj, rc=0, text=""):
            out.update({"rc": rc, "json": obj, "text": text or json.dumps(obj)})
            return out

        if name == "auth_bootstrap.py":
            provider = args[args.index("--provider") + 1]
            hint = args[args.index("--expect-email") + 1] if "--expect-email" in args else ""
            self.tick(60)
            phase2 = "--redirect-response" in args or "--complete-device-code" in args
            by_link = "--headless" in args or "--device-code" in args
            if provider in self.no_browser_class and not by_link and not phase2:
                return ret({"ok": False, "provider": provider, "error_class": "no_browser",
                            "line": "no browser"}, rc=1)
            if (provider in self.browserless or by_link) and not phase2:
                if self.headless_broken:
                    return ret({"ok": False, "provider": provider,
                                "error_class": "provider_error", "line": "x"}, rc=1)
                if provider == "google":
                    return ret({"ok": True, "provider": "google", "pending": "redirect_response",
                                "auth_url": "https://accounts.example/o/oauth2/auth?state=s1",
                                "error_class": "", "line": ""})
                return ret({"ok": True, "provider": "microsoft", "pending": "device_code",
                            "verification_uri": "https://microsoft.com/devicelogin",
                            "user_code": "ABCD-EFGH", "error_class": "", "line": ""})
            if "--redirect-response" in args:
                self.redirects.append(args[args.index("--redirect-response") + 1])
            if "--complete-device-code" in args:
                self.device_completions += 1
            if provider in self.auth_fail:
                cls = self.auth_fail[provider]
                return ret({"ok": False, "provider": provider, "error_class": cls,
                            "line": f"{provider} refused the sign-in ({cls}); run "
                                    f"{sf.RESUME} to try again"}, rc=1)
            email = self.email_for[provider]
            label = email.split("@", 1)[1].split(".")[0].capitalize()
            env_var = ("GOOGLE_REFRESH_TOKEN_" if provider == "google" else "MS_GRAPH_REFRESH_TOKEN_") + label.upper()
            token = GOOGLE_TOKEN if provider == "google" else MS_TOKEN
            import user_state
            user_state.upsert_env(env_var, token)
            return ret({"ok": True, "provider": provider, "email": email, "label": label,
                        "env_var": env_var, "error_class": "", "line": "",
                        "email_matches_hint": (email == hint) if hint else None})
        if name == "migrate_to_vault.py":
            vault = Path(args[args.index("--vault") + 1])
            (vault / "van-gogh").mkdir(parents=True, exist_ok=True)
            (self.state / "vault-pointer").write_text(str(vault), encoding="utf-8")
            return ret({"ok": True})
        if name == "vault/scaffold.py":
            vault = Path(args[0])
            for d in ("wiki/sources", "wiki/entities", "wiki/weekly", "wiki/projects"):
                (vault / d).mkdir(parents=True, exist_ok=True)
            (vault / "CLAUDE.md").write_text("# vault\n", encoding="utf-8")
            hot = vault / "wiki" / "hotcache.md"
            if not hot.exists():
                hot.write_text("# Hotcache\n\n## Active Threads\n\n## Jane's Action Items\n",
                               encoding="utf-8")
            return ret({"ok": True})
        if name in ("google_client.py", "microsoft_client.py"):
            rc = self.ms_smoke_rc if name == "microsoft_client.py" else 0
            label = args[args.index("--label") + 1]
            live = getattr(self, "live_addresses", {}).get(label, "")
            text = (f"OK [{label}] gmail: {live} (12 messages)" if name == "google_client.py"
                    else f"OK [{label}] graph /me: {live} (Jane Doe)") if live else "OK"
            return ret({}, rc=rc, text=text if rc == 0 else "error")
        if name == "scheduler_setup.py":
            self.scheduler_installs += 1
            self.jobs_loaded |= {"meeting-ingest", "ingest-workspace", "job-watch",
                                 "morning-coffee", "afternoon-tea", "week", "week-retro"}
            return ret({}, text="OK: installed")
        if name == "operator_research.py":
            if args[0] == "start":
                return ret({"ok": True, "pid": 77, "state": "running"})
            if args[0] == "status":
                return ret({"state": self.research_state})
            if args[0] == "show":
                return ret({"ok": True, "summary_md": "Jane Doe runs Acme.",
                            "proposal": {"proposals": {"priorities": [
                                {"name": "Close the Harbor PPA", "detail": "from acme.example"},
                                {"name": "Hire a head of sales", "detail": ""}]}}})
            if args[0] == "apply":
                return ret({"ok": True})
        if name == "warm_start.py":
            cmd = args[0]
            if cmd == "scan":
                return ret({"started": True, "pid": 88, "since": 60})
            if cmd == "wait-scan":
                if self.cap_at == "scan":
                    return ret({"ready": False, "error": "You're out of extra usage, resets 3:40pm"},
                               rc=1, text="You're out of extra usage, resets 3:40pm")
                if self.scan_error:
                    return ret({"ready": False, "error": self.scan_error}, rc=1)
                if self.scan_polls_left > 0:
                    self.scan_polls_left -= 1
                    return ret({"ready": False, "timeout": True}, rc=1)
                return ret({"ready": True, "counts": {"threads": 120}})
            if cmd == "priorities":
                self.priorities_set = json.loads(args[args.index("--set") + 1])
                return ret({"added": self.priorities_set, "backed_up": True})
            if cmd == "propose":
                if getattr(self, "propose_not_written", 0) > 0:
                    self.propose_not_written -= 1
                    return ret({"error": "the scan has not written its results yet"}, rc=1)
                if getattr(self, "propose_error", ""):
                    return ret({"error": self.propose_error}, rc=1)
                if self.cap_at == "propose":
                    return ret({"error": "Claude usage limit reached"}, rc=1,
                               text="Claude AI usage limit reached, resets at 3pm")
                existing = self._headings()
                gone = getattr(self, "vanish_after_day_change", set()) if \
                    getattr(self, "proposal_day", None) not in (None, self.clock.date()) else set()
                deals = [d for d in PROPOSED_DEALS if d["heading"] not in existing
                         and d["heading"] not in gone]
                self.last_proposal = {"deals": deals, "entities": list(PROPOSED_ENTITIES)}
                self.proposal_day = self.clock.date()
                path = self.state / "setup" / "warm-start" / str(self.proposal_day) / "proposal.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(self.last_proposal), encoding="utf-8")
                md = "\n".join(f"  [{i}] {d['heading']}" for i, d in enumerate(deals))
                return ret({**self.last_proposal, "summary_md": md, "proposal_path": str(path)})
            if cmd in ("apply", "verify") and "--proposal" in args:
                # The staged proposal lives in a dated folder: yesterday's is
                # not today's, and the real apply/verify then say so.
                assert args[args.index("--proposal") + 1] == str(
                    self.state / "setup" / "warm-start" / str(self.proposal_day) / "proposal.json")
                if self.proposal_day != self.clock.date():
                    return ret({"error": "No staged proposal. Run propose first.",
                                "need_propose": True}, rc=1)
            if cmd == "apply" and getattr(self, "apply_error", ""):
                return ret({"error": self.apply_error}, rc=1)
            if cmd == "apply":
                idx = [int(i) for i in args[args.index("--deals") + 1].split(",") if i]
                hot = self.vault / "wiki" / "hotcache.md"
                text = hot.read_text(encoding="utf-8")
                existing = self._headings()
                written, skipped = [], []
                covered = getattr(self, "duplicate_of", {})   # heading -> heading already tracked
                for i in idx:
                    heading = self.last_proposal["deals"][i]["heading"]
                    if heading in existing:
                        continue
                    if heading in covered and covered[heading] in existing:
                        skipped.append({"heading": heading, "reason": "already tracked",
                                        "matched": covered[heading]})
                        continue
                    text = text.replace("## Active Threads\n",
                                        f"## Active Threads\n\n### {heading}\n- stage: live\n", 1)
                    written.append(heading)
                if "### Thread Name" in text:
                    text = text.replace("### Thread Name\n- stage: sample\n", "")
                    skipped.append({"heading": "Thread Name", "reason": "sample removed", "matched": ""})
                hot.write_text(text, encoding="utf-8")
                for e in PROPOSED_ENTITIES:
                    p = self.vault / "wiki" / "entities" / f"{e['name']}.md"
                    if not p.exists():
                        p.write_text(f"# {e['name']}\n", encoding="utf-8")
                return ret({"deals": len(written), "written": written, "skipped": skipped,
                            "entities": len(PROPOSED_ENTITIES)})
            if cmd == "verify":
                existing = self._headings()
                covered = getattr(self, "duplicate_of", {})
                # Like the real verify: a fresh top list, so new_deals stays
                # non-zero whenever a subset was ticked (never the witness).
                new = [d for d in PROPOSED_DEALS if d["heading"] not in existing]
                reply = {"new_deals": len(new), "new_entities": 0}
                if "--picked" in args:
                    picked = [int(i) for i in args[args.index("--picked") + 1].split(",") if i]
                    rows = self.last_proposal["deals"]
                    landed = [i for i in picked if rows[i]["heading"] in existing
                              or covered.get(rows[i]["heading"]) in existing]
                    reply.update({"picked": picked, "landed": landed,
                                  "missing": [i for i in picked if i not in landed]})
                return ret(reply)
            if cmd == "ingest-start":
                self.ingest_started = True
                return ret({"started": True, "pid": 99, "limit": 10})
            if cmd == "ingest-finish":
                if not getattr(self, "ingest_started", False):
                    return ret({"filed": [], "failed": [], "deferred": [], "ingested": False,
                                "errors": ["no staged summaries: run ingest-start first"],
                                "counts": {"filed": 0, "failed": 0, "deferred": 0}})
                if self.summaries_polls_left > 0:
                    self.summaries_polls_left -= 1
                    return ret({"pending": True, "filed": [], "failed": [], "deferred": [],
                                "ingested": False, "errors": ["the prefetch is still running"]})
                src = self.vault / "wiki" / "sources"
                src.mkdir(parents=True, exist_ok=True)
                filed = []
                total = 10
                cap = self.cap_at == "summaries"
                if cap:
                    total = 6
                for i in range(total):
                    p = src / f"2026-09-{10 + i:02d} Meeting {i}.md"
                    if not p.exists():
                        p.write_text(f"---\nmeeting_id: m{i}\n---\n# Meeting {i}\n", encoding="utf-8")
                        filed.append(p.stem)
                deferred = [{"id": f"m{i}"} for i in range(total, 10)] if cap else []
                short = [{"id": f"m{i}", "title": f"Meeting {i}",
                          "reason": "over the install's time budget", "over_budget": True}
                         for i in range(getattr(self, "summary_only", 0))]
                # A meeting the notetaker has no transcript for is summary-only
                # too, but nothing is queued for it, so it must never be
                # promised a full read.
                short += [{"id": f"n{i}", "title": f"No transcript {i}",
                           "reason": "the notetaker returned no transcript",
                           "over_budget": False}
                          for i in range(getattr(self, "no_transcript", 0))]
                return ret({"filed": filed, "failed": [], "deferred": deferred, "ingested": True,
                            "summary_only": short,
                            "degraded": {"reason": "usage_cap", "deferred": 4} if cap else None,
                            "errors": [], "counts": {"filed": len(filed), "failed": 0,
                                                     "deferred": len(deferred)}})
            if cmd == "first-briefing":
                if self.cap_at == "briefing":
                    return ret({"errors": ["week: usage limit"]}, rc=1,
                               text="You've hit your limit, resets at 4pm")
                return ret({"which": args[args.index("--which") + 1], "data": "x.json", "errors": []})
        raise AssertionError(f"unexpected child {name} {args}")

    def _headings(self):
        hot = self.vault / "wiki" / "hotcache.md"
        try:
            return [l[4:].strip() for l in hot.read_text(encoding="utf-8").splitlines()
                    if l.startswith("### ")]
        except OSError:
            return []

    # ── the loop the skill runs ──────────────────────────────────────────
    def answer_for(self, prompt):
        pid = prompt["id"]
        if pid == "notetaker":
            return self.notetaker_answer
        if pid == "confirm":
            return "Yes, continue"
        if pid == "schedule":
            return self.schedule_answer
        if pid == "priorities":
            return [prompt["options"][0]["label"], "Win the Lakeside bid"]
        if pid == "deals":
            return self.deals_answer(prompt)
        if pid == "account_mismatch":
            return "Use this account anyway"
        if pid == "signin_link":
            if "Google" in prompt["question"]:
                return {"label": "I signed in",
                        "address": "http://localhost:8080/?state=s1&code=4/abc"}
            return "Done, I signed in"
        raise AssertionError(f"unexpected prompt {pid}")

    notetaker_answer = {"provider": "granola", "key": NOTETAKER_KEY}
    schedule_answer = "Keep these"

    def deals_answer(self, prompt):
        labels = [o["label"] for o in prompt["options"]
                  if o["label"] != "None of these" and "newsletter" not in o["label"]]
        return labels or ["None of these"]

    def write_briefings(self, agents):
        root = self.vault / "van-gogh"
        root.mkdir(parents=True, exist_ok=True)
        deal = self._headings()[0] if self._headings() else "nothing tracked"
        for a in agents:
            (root / f"{a['name']}.md").write_text(
                f"# {a['name']}\n\nTop of the list: {deal}.\n", encoding="utf-8")
        self.tick(150)

    def drive(self, max_turns=80, on_prompt=None):
        prompts, outs = [], []
        out = sf.run_next(None)
        for _ in range(max_turns):
            outs.append(out)
            kind = out["kind"]
            if kind == "prompt":
                prompts.append(out["prompt"]["id"])
                if on_prompt:
                    on_prompt(out)
                self.tick(20)
                out = sf.run_next({"id": out["prompt"]["id"], "value": self.answer_for(out["prompt"])})
            elif kind == "wait":
                self.tick(out["poll_seconds"])
                out = sf.run_next(None)
            elif kind == "dispatch":
                self.write_briefings(out["agents"])
                out = sf.run_next(None)
            else:
                return out, prompts, outs
        raise AssertionError("the loop never finished")


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def prepare(world):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sf.main(["prepare", "--run-id", "20261001-090000-ab12"])
    assert rc == 0
    return json.loads(buf.getvalue())


def vault_hash(vault):
    """{relative path: content hash}, with the vault's own absolute path
    normalised out so two temp installs compare equal."""
    out = {}
    for p in sorted(vault.rglob("*")):
        if p.is_file():
            text = p.read_text(encoding="utf-8").replace(str(vault), "<vault>")
            out[p.relative_to(vault).as_posix()] = hashlib.sha256(text.encode()).hexdigest()[:12]
    return out


def full_run(world):
    pre = prepare(world)
    out, prompts, outs = world.drive()
    return pre, out, prompts, outs


# ── P3, P9: the happy path, five prompts, an honest ledger ───────────────────

def test_happy_path_reaches_done_with_exactly_five_prompts(world):
    pre, out, prompts, outs = full_run(world)
    assert pre["ok"] and pre["keyfile"] and "credentials" not in pre["open"]
    assert out["kind"] == "done" and out["complete"] is True
    assert prompts == list(sf.PROMPT_IDS), prompts
    assert len(prompts) == 5
    # Sign-in was reached and at least one account passed.
    signins = [c for c in world.calls if c[0] == "auth_bootstrap.py"]
    assert len(signins) == 2
    stamp = ss.read_stamp()
    assert stamp["run_id"] == "20261001-090000-ab12"
    assert stamp["report"]["facts"]["accounts"]["detail"] == "2 signed in"
    assert not ss.exists()
    assert stamp["total"] and stamp["report"]["ok"]
    assert all(v == ss.DONE for v in stamp["boxes"].values()), stamp["boxes"]
    # The key file stayed in the private folder (P34).
    assert (world.state / "keyfile" / "van-gogh-key.json").exists()
    assert not world.key_file.exists()
    # Both briefing files exist, are non-empty and name a seeded deal.
    week = (world.vault / "van-gogh" / "week.md").read_text(encoding="utf-8")
    assert any(h in week for h in world._headings()), week
    mc = world.vault / "van-gogh" / "morning-coffee.md"
    assert mc.exists() and mc.stat().st_size > 0
    # The dispatch was two agents in one message, Morning Coffee before noon.
    dispatch = next(o for o in outs if o["kind"] == "dispatch")
    assert [a["name"] for a in dispatch["agents"]] == ["week", "morning-coffee"]
    # Ten meetings filed, 2 deals tracked, jobs installed once.
    assert len(list((world.vault / "wiki" / "sources").glob("*.md"))) == 10
    assert len(world._headings()) == 2
    assert world.scheduler_installs == 1
    assert world.priorities_set == {"acme": ["Close the Harbor PPA", "Win the Lakeside bid"]}


def test_ledger_measures_each_class_and_the_step_set(world):
    pre, out, prompts, outs = full_run(world)
    rows = ss.read_ledger()
    run_rows = [r for r in rows if r.get("run_id") == "20261001-090000-ab12"]
    assert run_rows and run_rows[0]["event"] == "start"
    steps = {r["step"] for r in run_rows if r["event"] == "step_begin"}
    assert steps == {"prepare", "plugin", "python", "credentials", "accounts", "meetings",
                     "vault", "jobs", "deals", "briefings"}
    report = out["report"]
    assert report["line"].startswith("TOTAL ") and " of 15:00" in report["line"]
    assert "DEGRADED" not in report["line"]
    for cls in ("machine", "human", "model"):
        assert report[cls] != "00:00", (cls, report)
    m = sf._measure(run_rows, "20261001-090000-ab12")
    # Human: 5 prompts at 20 s plus two sign-ins at 60 s each.
    assert m["human_s"] == pytest.approx(5 * 20 + 2 * 60 + 2 * 2, abs=1)
    # Model: the dispatch interval (150 s) plus the summaries job.
    assert m["model_s"] >= 150
    # Machine: every child call (2 s each), the waits, never a remainder.
    assert m["machine_s"] > 0
    assert m["total_s"] is not None and m["total_s"] < 15 * 60
    assert set(m["steps_run"]) == steps - {"prepare"}


def test_no_secret_value_reaches_ledger_state_stamp_or_logs(world):
    snapshots = []
    pre = prepare(world)
    out, prompts, outs = world.drive(on_prompt=lambda o: snapshots.append(
        ss.checklist_path().read_text(encoding="utf-8")))
    assert out["kind"] == "done"
    secrets = [GOOGLE_TOKEN, MS_TOKEN, NOTETAKER_KEY, CLIENT_SECRET]
    # The scanner must find a planted secret where it legitimately lives.
    env = (world.state / ".env").read_text(encoding="utf-8")
    assert GOOGLE_TOKEN in env and NOTETAKER_KEY in env
    haystacks = {"ledger": ss.timing_path().read_text(encoding="utf-8"),
                 "stamp": ss.stamp_path().read_text(encoding="utf-8")}
    for i, snap in enumerate(snapshots):
        haystacks[f"checklist-{i}"] = snap
    for p in world.logs.rglob("*"):
        if p.is_file():
            haystacks[str(p)] = p.read_text(encoding="utf-8", errors="replace")
    assert len(haystacks) >= 7
    for name, text in haystacks.items():
        for secret in secrets:
            assert secret not in text, (name, secret)


def test_real_entry_point_prints_one_json_object_per_command(world):
    """The script itself, as a child, for the commands that need no stub."""
    import subprocess
    env = dict(os.environ)
    r = subprocess.run([sys.executable, str(sf.APP_DIR / "setup_flow.py"), "status"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"exists": False, "stamp": False, "line": "not installed"}
    r = subprocess.run([sys.executable, str(sf.APP_DIR / "setup_flow.py"), "report"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 1
    assert json.loads(r.stdout)["line"].endswith("INCOMPLETE")
    r = subprocess.run([sys.executable, str(sf.APP_DIR / "setup_flow.py"), "nudge-job", "status"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and json.loads(r.stdout.splitlines()[-1])


# ── P6: killed at every step boundary, the re-run ends in the same state ─────

def final_state(world):
    stamp = ss.read_stamp()
    return {"boxes": stamp["boxes"], "vault": vault_hash(world.vault),
            "env": (world.state / ".env").read_text(encoding="utf-8"),
            "jobs": sorted(world.jobs_loaded), "checklist": ss.exists(),
            "keyfile": (world.state / "keyfile" / "van-gogh-key.json").exists(),
            "answers": {k: stamp["answers"][k] for k in ("notetaker", "schedule", "declined_deals")}}


@pytest.fixture
def uninterrupted(tmp_path, monkeypatch):
    world = World(tmp_path / "control", monkeypatch)
    full_run(world)
    return final_state(world)


@pytest.mark.parametrize("boundary", sorted(sf.STEPS))
def test_killed_at_a_step_boundary_the_rerun_finishes_the_same(tmp_path, monkeypatch,
                                                                 boundary, uninterrupted):
    world = World(tmp_path / "killed", monkeypatch)
    original = sf.STEPS[boundary]
    fired = {"n": 0}

    def crashing(data, answer=None):
        out = original(data, answer)
        if fired["n"] == 0:
            fired["n"] += 1
            raise KillSwitch(boundary)
        return out

    if boundary.split(":")[1] in ("plugin", "python", "credentials"):
        # These three are ticked inside `prepare`; the boundary is the end of
        # prepare itself, after the key file moved and the checklist saved.
        real_nudge = sf._nudge_job

        def crashing_nudge(action):
            if fired["n"] == 0:
                fired["n"] += 1
                raise KillSwitch(boundary)
            return real_nudge(action)

        monkeypatch.setattr(sf, "_nudge_job", crashing_nudge)
        with pytest.raises(KillSwitch):
            prepare(world)
        assert ss.exists() and ss.read_stamp() is None
        assert (world.state / "keyfile" / "van-gogh-key.json").exists()
        monkeypatch.setattr(sf, "_nudge_job", real_nudge)
        prepare(world)
        out, prompts, outs = world.drive()
        state = final_state(world)
        assert state == uninterrupted, boundary
        return

    monkeypatch.setitem(sf.STEPS, boundary, crashing)
    prepare(world)
    with pytest.raises(KillSwitch):
        world.drive()
    assert ss.exists(), "the checklist survives the crash"
    assert ss.read_stamp() is None
    monkeypatch.setitem(sf.STEPS, boundary, original)
    out, prompts, outs = world.drive()
    assert out["kind"] == "done" and out["complete"], (boundary, out)
    state = final_state(world)
    assert state == uninterrupted, boundary


# ── P7: idempotent ───────────────────────────────────────────────────────────

def test_stamp_present_means_already_installed_and_zero_steps(world):
    full_run(world)
    before_hash = vault_hash(world.vault)
    before_jobs = sorted(world.jobs_loaded)
    rows_before = len(ss.read_ledger())
    calls_before = len(world.calls)
    out = sf.run_next(None)
    assert out["kind"] == "done" and out.get("already_installed") is True
    rows = ss.read_ledger()[rows_before:]
    assert [r for r in rows if r["event"] in ("step_begin", "step_end")] == []
    assert vault_hash(world.vault) == before_hash
    assert sorted(world.jobs_loaded) == before_jobs
    assert len(world.calls) == calls_before
    assert not ss.exists()


def test_stamp_removed_with_everything_on_disk_reruns_with_zero_new(world):
    full_run(world)
    pages_before = sorted(p.name for p in (world.vault / "wiki" / "sources").glob("*.md"))
    assert len(pages_before) == 10
    headings_before = world._headings()
    installs_before = world.scheduler_installs
    assert ss.remove_stamp()
    world.scan_polls_left = 0
    world.summaries_polls_left = 0
    out, prompts, outs = world.drive()
    assert out["kind"] == "done" and out["complete"]
    stamp = ss.read_stamp()
    assert all(v == ss.DONE for v in stamp["boxes"].values()), stamp["boxes"]
    assert sorted(p.name for p in (world.vault / "wiki" / "sources").glob("*.md")) == pages_before
    assert world._headings() == headings_before
    assert world.scheduler_installs == installs_before, "0 duplicate jobs"
    assert stamp["boxes"]["deals"] == ss.DONE


# ── P24, P34: the checklist and the key file through a run ───────────────────

def test_checklist_and_key_file_lifecycle(world):
    seen = []

    def on_prompt(out):
        data = ss.load()
        seen.append((out["prompt"]["id"], {b["key"]: b["status"] for b in data["boxes"]}))
        assert [b["key"] for b in data["boxes"]] == list(ss.KEYS)
        assert (world.state / "keyfile" / "van-gogh-key.json").exists()
        assert not world.key_file.exists(), "gone from Downloads at the credentials step"

    prepare(world)
    assert (world.state / "keyfile" / "van-gogh-key.json").exists()
    if sys.platform != "win32":
        assert (world.state / "keyfile").stat().st_mode & 0o777 == 0o700
    out, prompts, outs = world.drive(on_prompt=on_prompt)
    assert out["complete"]
    assert len(seen) == 5
    first = seen[0][1]
    assert first["plugin"] == first["python"] == first["credentials"] == first["accounts"] == ss.DONE
    assert first["vault"] == ss.OPEN
    assert not ss.exists() and ss.read_stamp() is not None
    assert (world.state / "keyfile" / "van-gogh-key.json").exists()


def test_degraded_run_keeps_the_checklist_open_boxes_named_no_stamp_key_file_kept(world):
    world.cap_at = "summaries"
    prepare(world)
    out, prompts, outs = world.drive()
    assert out["kind"] == "done" and out["complete"] is False
    assert out["degraded"]["box"] == "meetings"
    assert ss.exists() and ss.read_stamp() is None
    data = ss.load()
    assert [b["key"] for b in ss.open_boxes(data)] == ["meetings"]
    assert ss.box(data, "meetings")["status"] == ss.PENDING
    assert ss.box(data, "meetings")["detail"] == "waiting on the Claude usage limit: 6 filed, 4 waiting"
    assert (world.state / "keyfile" / "van-gogh-key.json").exists()
    # One situation, one statement, one command, and the limit in plain words.
    text = out["closing_text"]
    assert text.startswith(
        "Setup is almost done: 6 of your 10 meetings are filed. The other 4 are waiting because "
        "your Claude plan's usage limit was reached, and they file by themselves overnight "
        "while this computer is on. "
        "To check on them, in Claude, type /van-gogh:install-van-gogh\n")
    assert "/van-gogh:meeting-ingest" not in text and "usage cap" not in text
    assert "Installed in" not in text and "Still to do" not in text
    report = sf.build_report(data)
    assert "DEGRADED" in report["line"] and report["ok"] is False


def test_an_artifact_deleted_mid_run_reads_open_again(world):
    prepare(world)
    deleted = {"done": False}

    def on_prompt(out):
        if out["prompt"]["id"] == "schedule" and not deleted["done"]:
            (world.vault / "van-gogh" / "config.json").unlink()
            deleted["done"] = True
            data = ss.load()
            assert ss.box(data, "vault")["status"] == ss.DONE
            ss.verify(data, checks=sf.checks())
            assert ss.box(data, "vault")["status"] == ss.OPEN

    out, prompts, outs = world.drive(on_prompt=on_prompt)
    assert out["complete"]
    rows = [r for r in ss.read_ledger() if r["event"] == "step_begin" and r["step"] == "vault"]
    assert len(rows) >= 2, "the vault step ran again after its artifact vanished"
    assert (world.vault / "van-gogh" / "config.json").exists()


# ── P30: finish semantics ────────────────────────────────────────────────────

def test_no_notetaker_completes_with_meetings_not_applicable(world):
    world.notetaker_answer = "None, or another notetaker"
    pre, out, prompts, outs = full_run(world)
    assert out["complete"]
    assert ss.read_stamp()["boxes"]["meetings"] == ss.NOT_APPLICABLE
    assert not any(c[1][:1] == ["ingest-start"] for c in world.calls if c[0] == "warm_start.py")
    assert ("No meeting notes are filed, because no notetaker was connected. When you have "
            "one, in Claude, type /van-gogh:update-settings; it asks for the key.") in out["closing_text"]
    assert "/van-gogh:meeting-ingest" not in out["closing_text"]
    assert sf.HISTORY_LINE not in out["closing_text"], "no notetaker, no history to get"


def test_the_closing_says_the_rest_of_the_meeting_history_comes_next(world):
    pre, out, prompts, outs = full_run(world)
    assert out["complete"]
    lines = out["closing_text"].splitlines()
    assert lines.count(sf.HISTORY_LINE) == 1
    assert "last 60 days" in sf.HISTORY_LINE and sf.SETUP_MEETING_DAYS == 60
    assert lines[-1] == "You can close this window."


def test_no_deals_ticked_completes_with_deals_skipped(world):
    world.deals_answer = lambda prompt: ["None of these"]
    pre, out, prompts, outs = full_run(world)
    assert out["complete"]
    stamp = ss.read_stamp()
    assert stamp["boxes"]["deals"] == ss.SKIPPED
    assert sorted(stamp["answers"]["declined_deals"]) == [0, 1, 2]
    assert ss.read_stamp()["boxes"]["deals"] == ss.SKIPPED
    assert ("No deals are tracked yet, because none were ticked. "
            "In Claude, /van-gogh:warm-start proposes them again.") in out["closing_text"]
    assert "people" not in out["closing_text"]
    assert not any(c[1][:1] == ["apply"] for c in world.calls if c[0] == "warm_start.py")


def test_no_background_jobs_completes_with_jobs_skipped(world):
    world.schedule_answer = "Schedule nothing"
    pre, out, prompts, outs = full_run(world)
    assert out["complete"]
    assert ss.read_stamp()["boxes"]["jobs"] == ss.SKIPPED
    assert world.scheduler_installs == 0
    cfg = json.loads((world.vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert cfg["digest"]["enabled"] is False
    text = out["closing_text"]
    # One fact, one line: the skipped-box line is the whole statement, and
    # the one command it needs is said once, in the line that introduces
    # Vincent.
    assert text.count(sf.VINCENT_COMMAND) == 1
    assert "No briefings are scheduled" not in text and "an email arrives" not in text
    assert ("Nothing is scheduled, as you chose: no briefing is emailed and nothing runs by "
            "itself. To turn something on, ask Vincent to schedule it.") in text
    for key in ("finance", "contact_capture", "meeting_prep", "notes"):
        assert cfg[key]["enabled"] is False, key
    assert cfg["workbench"]["autostart"] is False
    assert all(v["enabled"] is False for k, v in cfg["cadence"].items() if isinstance(v, dict))


def _shows(o):
    return (o.get("prompt") or {}).get("show") or o.get("show") or ""


def test_what_was_scheduled_is_said_once_right_after_it_is_installed(world):
    """The client is told what runs by itself at the moment it is installed,
    on the very next thing printed, never left to ask afterwards."""
    prepare(world)
    out, prompts, outs = world.drive()
    assert out["complete"] and world.scheduler_installs == 1
    told = [i for i, o in enumerate(outs) if "Scheduled on this computer now:" in _shows(o)]
    assert len(told) == 1, told
    first = outs[told[0]]
    assert "Scheduled on this computer now: 13 jobs" in _shows(first)
    import schedule_plan
    assert schedule_plan.NAMES["digest-morning-coffee"][0] in _shows(first)
    # The turn before it was the schedule question being answered: nothing
    # else happened in between that could hide it.
    asked = [i for i, o in enumerate(outs)
             if o["kind"] == "prompt" and o["prompt"]["id"] == "schedule"]
    assert asked and told[0] == asked[-1] + 1


def test_nothing_is_announced_when_nothing_is_scheduled(world):
    world.schedule_answer = "Schedule nothing"
    prepare(world)
    out, prompts, outs = world.drive()
    assert out["complete"]
    assert not any("Scheduled on this computer now:" in _shows(o) for o in outs)


def test_under_codex_the_schedule_table_is_inside_the_question(world, monkeypatch):
    """Codex asks in plain text; the table is part of the question asked as
    given, so the client cannot be asked to keep a schedule they never saw."""
    prepare(world)
    monkeypatch.setattr(sf.host, "is_codex", lambda: True)
    seen = {}
    out, prompts, outs = world.drive(
        on_prompt=lambda o: seen.setdefault(o["prompt"]["id"], o["prompt"]))
    spec = seen["schedule"]
    assert "show" not in spec
    assert spec["question"].startswith("What runs by itself on this computer")
    assert spec["question"].endswith("Keep these, or change something first?")
    assert "ON (13)" in spec["question"]


def test_the_schedule_prompt_shows_every_job_and_has_three_outcomes(world):
    """The whole table is shown before the question, read from the config a
    yes would write, and there are three ways to answer it."""
    seen = {}
    prepare(world)
    out, prompts, outs = world.drive(on_prompt=lambda o: seen.setdefault(o["prompt"]["id"], o["prompt"]))
    assert out["complete"]
    spec = seen["schedule"]
    assert [o["label"] for o in spec["options"]] == [
        sf.SCHEDULE_KEEP, sf.SCHEDULE_CHANGE, sf.SCHEDULE_NONE]
    import job_registry
    import schedule_plan
    keys = [j.key for j in job_registry.jobs()]
    assert len(keys) == 18
    for key in keys:
        assert schedule_plan.NAMES[key][0] in spec["show"], key
    on_part, off_part = spec["show"].split("\nOFF (")
    assert "ON (13)" in on_part and off_part.startswith("5)")
    tpl = json.loads((sf.PLUGIN_ROOT / "config.template.json").read_text(encoding="utf-8"))
    briefings = tpl["digest"]["briefings"]
    assert set(briefings) == set(sf.BRIEFING_TITLES)
    for key, title in sf.BRIEFING_TITLES.items():
        hour, minute = (int(x) for x in briefings[key]["time"].split(":"))
        clock = f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"
        row = next(ln for ln in on_part.splitlines() if ln.strip().startswith(title + "  "))
        assert f"at {clock}" in row, (title, clock, row)
    assert "one email each morning at 6:15 AM" in on_part
    assert {j["job"] for j in spec["jobs"]} == set(keys) - set(schedule_plan.FIXED)
    assert ss.read_stamp()["answers"]["schedule"] == "all"
    cfg = json.loads((world.vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    rows = {r["key"]: r["on"] for r in schedule_plan.table(cfg)}
    assert {k for k, on in rows.items()
            if on and k not in schedule_plan.WITH_SCHEDULE} == set(schedule_plan.SETUP_ON)
    assert cfg["meeting_prep"]["mode"] == "daily"


def test_a_change_is_laid_over_the_table_and_the_question_is_asked_again(world):
    """Change something: the changes are checked by code, the table comes
    back with them marked, what could not be placed is named, and only a
    later Keep these writes anything."""
    seen = []
    answers = iter([
        "Change something",
        {"changes": [{"job": "coffee", "time": "6:30"},
                     {"job": "finance", "on": True},
                     {"job": "bagel poller", "on": True},
                     {"job": "radar", "on": False}]},
        "Keep these"])

    def on_prompt(o):
        if o["prompt"]["id"] == "schedule":
            seen.append(o["prompt"])
            world.schedule_answer = next(answers)

    prepare(world)
    out, prompts, outs = world.drive(on_prompt=on_prompt)
    assert out["complete"]
    assert len(seen) == 3
    assert seen[1]["question"].startswith(sf.SCHEDULE_ASK)
    assert world.scheduler_installs == 1, "nothing is scheduled until the client keeps a table"
    third = seen[2]
    assert 'I could not tell which job "bagel poller" is.' in third["question"]
    assert "Morning Coffee: on, weekdays at 6:30 AM." in third["question"]
    marked = [ln for ln in third["show"].splitlines() if ln.endswith("(changed)")]
    assert len(marked) == 3, marked
    cfg = json.loads((world.vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert cfg["digest"]["briefings"]["morning-coffee"]["time"] == "06:30"
    assert cfg["finance"]["enabled"] is True
    assert cfg["cadence"]["relationship-radar"]["enabled"] is False
    assert ss.read_stamp()["answers"]["schedule"] == "custom"
    assert prompts.count("schedule") == 3


def test_switching_every_job_off_one_at_a_time_is_the_same_as_scheduling_nothing(world):
    import schedule_plan
    answers = iter([
        {"changes": [{"job": k, "on": False} for k in schedule_plan.SETUP_ON]},
        "Keep these"])

    def on_prompt(o):
        if o["prompt"]["id"] == "schedule":
            world.schedule_answer = next(answers)

    prepare(world)
    out, prompts, outs = world.drive(on_prompt=on_prompt)
    assert out["complete"]
    assert ss.read_stamp()["boxes"]["jobs"] == ss.SKIPPED
    assert world.scheduler_installs == 0


def test_the_fallback_priorities_each_say_what_they_change(world):
    world.research_state = "running"
    seen = {}
    prepare(world)
    world.drive(on_prompt=lambda o: seen.setdefault(o["prompt"]["id"], o["prompt"]))
    options = seen["priorities"]["options"]
    assert [o["label"] for o in options] == [label for label, _ in sf.GENERIC_PRIORITIES]
    descriptions = [o["description"] for o in options]
    assert len(set(descriptions)) == 3 and all(d.startswith("Briefings ") for d in descriptions)


def test_prepare_never_touches_the_real_scheduler_from_an_overridden_state_dir(world, monkeypatch):
    """A temp state dir once registered com.monet.setup-nudge in the real
    launchd session. With the state dir overridden the job is skipped and
    said so, unless VAN_GOGH_NUDGE_JOB=1 forces it."""
    monkeypatch.delenv("VAN_GOGH_NUDGE_JOB")
    pre = prepare(world)
    assert pre["ok"] is True and pre["nudge_job"] is False
    assert pre["nudge_job_note"] == "nudge job skipped (state dir overridden)"
    assert ss.load()["nudges"]["job"] == "nudge job skipped (state dir overridden)"
    assert world.nudge_calls == [], "setup_nudge was never asked to install"
    assert sf._nudge_job("remove")["skipped"] is True and world.nudge_calls == []
    monkeypatch.setenv("VAN_GOGH_NUDGE_JOB", "1")
    assert sf._nudge_job("install")["ok"] is True
    assert world.nudge_calls == [["job", "install"]]
    # With no override at all (a real install) the job is installed as before.
    monkeypatch.delenv("VAN_GOGH_NUDGE_JOB")
    monkeypatch.delenv("VAN_GOGH_STATE_DIR")
    assert "skipped" not in sf._nudge_job("status")


def test_accounts_and_vault_can_never_be_skipped(world):
    data = ss.new("r1")
    for key in ("accounts", "vault"):
        with pytest.raises(ss.ChecklistError):
            ss.set_status(data, key, ss.SKIPPED)


@pytest.mark.parametrize("where,box", [("scan", "deals"), ("summaries", "meetings"),
                                       ("briefing", "briefings")])
def test_a_cap_leaves_that_step_pending_and_a_resume_finishes_without_redoing(world, where, box):
    world.cap_at = where
    prepare(world)
    out, prompts, outs = world.drive()
    assert out["kind"] == "done" and out["complete"] is False, (where, out)
    data = ss.load()
    assert data["degraded"]["box"] == box
    assert ss.box(data, box)["status"] == ss.PENDING
    assert ss.read_stamp() is None
    done_before = {b["key"] for b in data["boxes"] if b["status"] == ss.DONE}
    assert "accounts" in done_before and "vault" in done_before
    signins = len([c for c in world.calls if c[0] == "auth_bootstrap.py"])
    installs = world.scheduler_installs
    migrates = len([c for c in world.calls if c[0] == "migrate_to_vault.py"])
    # The cap lifts; the resume finishes the pending box only.
    world.cap_at = None
    out, prompts2, outs = world.drive()
    assert out["kind"] == "done" and out["complete"] is True, (where, out)
    assert set(prompts2) <= {"priorities", "deals"}, prompts2
    assert len(prompts + prompts2) == len(set(prompts + prompts2)), "no prompt is asked twice"
    assert len([c for c in world.calls if c[0] == "auth_bootstrap.py"]) == signins
    assert world.scheduler_installs == installs
    assert len([c for c in world.calls if c[0] == "migrate_to_vault.py"]) == migrates
    assert ss.read_stamp()["boxes"][box] == ss.DONE


def test_closing_text_names_each_skipped_box_and_its_later_command(world):
    world.notetaker_answer = "None, or another notetaker"
    world.deals_answer = lambda prompt: ["None of these"]
    world.schedule_answer = "Schedule nothing"
    pre, out, prompts, outs = full_run(world)
    text = out["closing_text"]
    for cmd in (sf.LATER["jobs"], sf.LATER["deals"], sf.SETTINGS_COMMAND):
        assert text.count(cmd) == 1, (cmd, text)
    assert "Nothing is scheduled, as you chose" in text
    assert "No deals are tracked yet, because none were ticked." in text
    assert "No meeting notes are filed, because no notetaker was connected." in text
    assert text.count("team@vangogh.example") == 1, "the help address is said once"
    assert "no notetaker was connected" in text
    assert "You can close this window." in text


# ── P12: a cap is the cap, another provider's limit is not ───────────────────

def test_another_providers_usage_limit_is_not_the_cap(world):
    import failure_class
    assert failure_class.classify("Granola API: usage limit exceeded for plan") != "quota" \
        or not failure_class.is_usage_cap("Granola API: usage limit exceeded for plan")
    assert failure_class.is_usage_cap("You're out of extra usage, resets 3:40pm")
    assert sf._capped({"json": {"degraded": {"reason": "usage_cap"}}, "text": ""})
    assert not sf._capped({"json": {}, "text": "some unknown error"})


# ── P31: the stamp is a hint, disk is truth ──────────────────────────────────

def test_verify_stamp_local_is_fast_and_says_already_installed_with_the_network_off(world):
    full_run(world)
    calls = []

    def network_off(*a, **k):
        calls.append(a)
        raise OSError("network off")

    import contextlib
    import io
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sf, "_run_script_raw", network_off)
        t0 = time.monotonic()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = sf.main(["verify-stamp"])
        elapsed = time.monotonic() - t0
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["already_installed"] is True and out["line"] == "already installed"
    assert elapsed < 5
    assert calls == [], "the local check makes no network call"
    assert not ss.exists()


def test_verify_stamp_live_refreshes_one_token_at_most_once_a_day(world):
    full_run(world)
    world.calls.clear()
    assert sf.verify_stamp(live=True)["live"] is True
    refreshes = [c for c in world.calls if c[0] in ("google_client.py", "microsoft_client.py")]
    assert len(refreshes) == 1 and refreshes[0][1] == ["--label", "Acme"]
    world.tick(3600)
    assert sf.verify_stamp(live=True)["live"] is False
    assert len([c for c in world.calls if c[0] in ("google_client.py", "microsoft_client.py")]) == 1
    world.tick(24 * 3600)
    assert sf.verify_stamp(live=True)["live"] is True
    assert len([c for c in world.calls if c[0] in ("google_client.py", "microsoft_client.py")]) == 2


def test_a_revoked_token_or_an_unloaded_job_reopens_the_box_and_nudges(world):
    full_run(world)
    # Revoked token: the live refresh fails.
    world.tick(48 * 3600)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sf, "refresh_state", lambda provider, label: sf.REJECTED)
        out = sf.verify_stamp(live=True)
    assert out["already_installed"] is False and out["reopened"] == ["accounts"]
    assert "already installed" not in out["line"]
    assert ss.RESUME_COMMAND in out["line"] and "sign in" in out["line"]
    data = ss.load()
    assert ss.box(data, "accounts")["status"] == ss.OPEN
    assert all(b["status"] in ss.RESOLVED for b in data["boxes"] if b["key"] != "accounts")
    assert ["job", "install"] in world.nudge_calls
    # Unloaded job: a fresh stamp, then a job disappears.
    ss.remove()
    world.jobs_loaded.discard("week")
    out = sf.verify_stamp(live=False)
    assert out["already_installed"] is False and out["reopened"] == ["jobs"]
    assert "already installed" not in out["line"]
    data = ss.load()
    assert ss.box(data, "jobs")["detail"] == "1 of 7 jobs not loaded"


def test_reset_removes_checklist_stamp_and_nudge_job_together(world, monkeypatch):
    """Uninstall's `reset`: the stamp, a checklist AND the nudge job all exist
    first (a stamped install that was reopened by a broken box), and all three
    are gone after."""
    full_run(world)
    # Reopen a box so a checklist sits beside the stamp, and install the job.
    world.jobs_loaded.discard("week")
    assert sf.verify_stamp(live=False)["reopened"] == ["jobs"]
    job = {"state": "not installed"}

    def nudge_main(argv):
        world.nudge_calls.append(list(argv))
        if argv[:2] == ["job", "install"]:
            job["state"] = "loaded"
        elif argv[:2] == ["job", "remove"]:
            job["state"] = "not installed"
        print(json.dumps({"ok": True, "state": job["state"]}))
        return 0

    monkeypatch.setitem(sys.modules, "setup_nudge", types.SimpleNamespace(main=nudge_main))
    sf._nudge_job("install")
    assert ss.read_stamp() is not None, "stamp present"
    assert ss.exists(), "checklist present"
    assert job["state"] == "loaded", "nudge job registered"
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert sf.main(["reset"]) == 0
    out = json.loads(buf.getvalue())
    assert out["removed"] == {"checklist": True, "stamp": True}
    assert ss.read_stamp() is None and not ss.exists()
    assert job["state"] == "not installed" and out["nudge_job"] is True
    assert world.nudge_calls[-1] == ["job", "remove"]


# ── P33: keep-awake, the bell, a clock time in the closing text ──────────────

def test_keep_awake_holder_starts_on_the_first_next_and_ends_at_finish(world):
    """The inhibitor lives in a detached holder (`_keepawake`), not in the
    short-lived `next` process: the first `next` spawns it, finish ends it."""
    prepare(world)
    assert not any("_keepawake" in cmd for cmd in world.detached)
    sf.run_next(None)
    holders = [cmd for cmd in world.detached if "_keepawake" in cmd]
    assert len(holders) == 1 and holders[0][:2] == [sys.executable, str(sf.APP_DIR / "setup_flow.py")]
    assert sf._keepawake_pid_path().read_text(encoding="utf-8").strip() == "4242"
    out, prompts, outs = world.drive()
    assert out["complete"]
    assert not sf._keepawake_pid_path().exists(), "finish ended the holder"


def test_keepawake_loop_inhibits_while_the_checklist_is_open(world):
    """The holder's body with the inhibitor stubbed: start on its own pid,
    stop when the checklist resolves, or at the deadline."""
    calls = []
    world_mod = types.SimpleNamespace(start=lambda pid: calls.append(("start", pid)),
                                      stop=lambda: calls.append(("stop",)),
                                      bell=lambda: None)
    sys.modules["keep_awake"] = world_mod
    ss.save(ss.new("r-hold"))
    ticks = {"n": 0}

    def sleep(_):
        ticks["n"] += 1
        if ticks["n"] == 3:
            ss.remove()

    assert sf.keepawake_loop(max_seconds=600, poll=1, clock=lambda: ticks["n"], sleep=sleep) == "checklist gone"
    assert calls == [("start", os.getpid()), ("stop",)]
    calls.clear()
    data = ss.new("r-hold")
    ss.save(data)
    assert sf.keepawake_loop(max_seconds=2, poll=1, clock=lambda: ticks["n"], sleep=lambda s: ticks.__setitem__("n", ticks["n"] + 1)) == "timeout"
    assert calls[-1] == ("stop",)
    for key in ss.KEYS:
        ss.set_status(data, key, ss.DONE)
    ss.save(data)
    assert sf.keepawake_loop(max_seconds=600, poll=1) == "checklist resolved"


def test_a_terminal_error_ends_the_keep_awake_holder(world):
    world.auth_fail["google"] = "admin_consent"
    prepare(world)
    out = sf.run_next(None)
    assert out["kind"] == "error"
    assert not sf._keepawake_pid_path().exists()


def test_bell_only_after_a_prompt_has_waited_more_than_thirty_seconds(world):
    prepare(world)
    out = sf.run_next(None)
    assert out["kind"] == "prompt" and out["prompt"]["id"] == "notetaker"
    # A detached timer was armed for this prompt.
    assert any("_bell" in cmd and "notetaker" in cmd for cmd in world.detached)
    data = ss.load()
    assert sf.bell_due(data, "notetaker", now=world.clock + timedelta(seconds=29)) is False
    assert sf.bell_due(data, "notetaker", now=world.clock + timedelta(seconds=31)) is True
    sf.run_next({"id": "notetaker", "value": "None, or another notetaker"})
    data = ss.load()
    assert sf.bell_due(data, "notetaker", now=world.clock + timedelta(seconds=90)) is False


def test_closing_text_carries_a_clock_time_in_the_clients_timezone(world):
    pre, out, prompts, outs = full_run(world)
    text = out["closing_text"]
    # Thursday 2026-10-01 in New York: tomorrow is Friday, a briefing day.
    # Thursday 2026-10-01 in New York: the next briefing day is Friday, by name.
    assert ("Your first briefing email arrives Friday at 7:00 AM. If it is not there by 9:00 AM, "
            "open Claude and type /van-gogh:morning-coffee, or write to team@vangogh.example.") in text
    assert "Tomorrow" not in text
    import re as _re
    assert _re.match(r"Installed in \d+ minutes? \d+ seconds?\.\n", text), text[:60]
    assert text.startswith("Installed in ")
    assert text.count("team@vangogh.example") == 1, "the help address is said once"


def test_first_email_line_skips_the_weekend():
    cfg = {"digest": {"enabled": True, "briefings": {"morning-coffee": {
        "time": "07:00", "days": ["monday", "tuesday", "wednesday", "thursday", "friday"]}}}}
    friday = datetime(2026, 10, 2, 15, 0, tzinfo=TZ)
    line = sf._first_email_line(cfg, friday)
    assert line.startswith("Your first briefing email arrives Monday at 7:00 AM.")
    assert line.endswith("open Claude and type /van-gogh:morning-coffee."), "no support address, no clause"


# ── prompts: the account mismatch and a bad notetaker key re-ask the same id ──

def test_account_mismatch_is_the_sixth_prompt_only_when_the_address_differs(world):
    world.email_for["google"] = "other@elsewhere.example"
    pre, out, prompts, outs = full_run(world)
    assert out["complete"]
    assert prompts == ["account_mismatch"] + list(sf.PROMPT_IDS)
    cfg = json.loads((world.vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert cfg["accounts"][0]["email"] == "other@elsewhere.example"


def test_a_bad_notetaker_key_is_never_written_and_the_prompt_repeats(world):
    prepare(world)
    out = sf.run_next(None)
    assert out["prompt"]["id"] == "notetaker"
    out = sf.run_next({"id": "notetaker", "value": {"provider": "granola", "key": "wrong-key"}})
    assert out["kind"] == "prompt" and out["prompt"]["id"] == "notetaker"
    assert "did not work" in out["line"]
    env = (world.state / ".env").read_text(encoding="utf-8")
    assert "GRANOLA_API_KEY" not in env and "wrong-key" not in env
    out = sf.run_next({"id": "notetaker", "value": {"provider": "granola", "key": NOTETAKER_KEY}})
    assert out["prompt"]["id"] == "confirm"
    env = (world.state / ".env").read_text(encoding="utf-8")
    assert f"GRANOLA_API_KEY={NOTETAKER_KEY}" in env


def test_a_sign_in_error_is_one_line_with_the_resume_command(world):
    world.auth_fail["google"] = "admin_consent"
    prepare(world)
    out = sf.run_next(None)
    assert out["kind"] == "error" and out["box"] == "accounts"
    assert len(out["line"]) <= 120 and sf.RESUME in out["line"]
    assert out["line"] == ("Your IT admin must approve Van Gogh for Google first. Once they have, "
                           "in Claude, type /van-gogh:install-van-gogh")
    assert ss.exists() and ss.read_stamp() is None


def test_every_error_line_fits_and_says_where_to_type():
    """The setup line is pasted into Terminal and the checklist drills "never
    into Claude" for it, so a bare "type X" lands in the wrong window."""
    lines = sf.all_error_lines("team@example.com")
    assert len(lines) == len(sf.ERROR_LINES) + 1 + 2 * len(sf.SIGNIN_LINES) == 34
    for line in lines:
        assert len(line) <= 120, (len(line), line)
        if sf.RESUME in line:
            assert f"in Claude, type {sf.RESUME}" in line, line
        else:
            assert "Terminal (PowerShell on Windows)" in line or \
                line.startswith("The sign-in link could not be made"), line
    assert sum(sf.RESUME not in l for l in lines) == 3, "no plugin, no Python, no way to sign in"
    # The help address rides on the settings line only while it fits.
    assert sf.settings_line("team@example.com").endswith("; if it repeats, write to team@example.com")
    assert sf.settings_line("") == sf.settings_line("a-very-long-support-address@some-company.example")
    assert sf.no_browser_line("team@example.com") == (
        "The sign-in link could not be made on this computer. Write to team@example.com and we "
        "will sign you in another way.")
    assert sf.no_browser_line("") == (
        "The sign-in link could not be made on this computer. Ask the Van Gogh team to sign you "
        "in another way.")
    for provider, name in (("google", "Google"), ("microsoft", "Microsoft")):
        line = sf.signin_line(provider, "unverified_app", "team@example.com")
        assert line == (f"{name} blocked this account. Write to team@example.com; once approved, "
                        "in Claude, type /van-gogh:install-van-gogh") and len(line) <= 120
    assert sf.signin_line("google", "unverified_app") == (
        "Google blocked this account. Ask the Van Gogh team to add you, then in Claude, type "
        "/van-gogh:install-van-gogh")
    assert sf.signin_line("google", "no_credential") == (
        "Save the key file we sent you into your Downloads folder, then in Claude, type "
        "/van-gogh:install-van-gogh")
    assert sf.signin_line("microsoft", "something new").startswith("Microsoft refused the sign-in.")


def test_a_folder_that_cannot_be_made_reasks_the_confirm_screen(world):
    """Not a terminal error: the client cannot repair a folder from an error
    line, but "Change something" on the confirm screen is one tap away."""
    real = world.run_script
    refused = {"n": 0}

    def run(name, args, timeout=600, input_text=None, cwd=None):
        if name == "migrate_to_vault.py" and refused["n"] == 0:
            refused["n"] += 1
            world.calls.append((name, [str(a) for a in args]))
            return {"rc": 1, "json": {}, "text": "PermissionError"}
        return real(name, args, timeout, input_text, cwd)

    world.run_script = run
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(sf, "_run_script_raw", run)
        prepare(world)
        out = sf.run_next(None)
        out = sf.run_next({"id": "notetaker", "value": "None, or another notetaker"})
        out = sf.run_next({"id": "confirm", "value": "Yes, continue"})
        assert out["kind"] == "prompt" and out["prompt"]["id"] == "confirm"
        assert out["prompt"]["question"].startswith(
            f"The folder {world.vault} could not be created. Choose Change something and give another folder.")
        assert out["line"] == out["prompt"]["question"].split("\n")[0]
        other = world.home / "notes"
        out = sf.run_next({"id": "confirm", "value": {"vault_path": str(other)}})
        assert out["prompt"]["id"] == "confirm" and out["prompt"]["question"].startswith("Updated.")
        world.vault = other
        out = sf.run_next({"id": "confirm", "value": "Yes, continue"})
        assert out["prompt"]["id"] == "schedule"
        assert (other / "van-gogh" / "config.json").exists()


def test_an_account_that_fails_its_test_call_is_signed_in_again_on_resume(world):
    """The error line says "to sign in again", so the resume must really
    open the sign-in for that account, not just repeat the test call."""
    real = world.run_script
    failed = {"n": 0}

    def run(name, args, timeout=600, input_text=None, cwd=None):
        if name == "google_client.py" and failed["n"] == 0:
            failed["n"] += 1
            world.calls.append((name, [str(a) for a in args]))
            return {"rc": 1, "json": {}, "text": "invalid_grant"}
        return real(name, args, timeout, input_text, cwd)

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(sf, "_run_script_raw", run)
        prepare(world)
        sf.run_next(None)
        sf.run_next({"id": "notetaker", "value": "None, or another notetaker"})
        out = sf.run_next({"id": "confirm", "value": "Yes, continue"})
        assert out["kind"] == "error" and out["box"] == "vault"
        assert out["line"] == ("Van Gogh could not read your Google account just now. To sign in "
                               "again, in Claude, type /van-gogh:install-van-gogh")
        signins = [c for c in world.calls if c[0] == "auth_bootstrap.py"]
        assert [c[1][1] for c in signins] == ["google", "microsoft"]
        out = sf.run_next(None)
        signins = [c for c in world.calls if c[0] == "auth_bootstrap.py"]
        assert [c[1][1] for c in signins] == ["google", "microsoft", "google"], "Google only, again"
        assert out["kind"] == "prompt" and out["prompt"]["id"] == "schedule"
        data = ss.load()
        assert [a["provider"] for a in data["answers"]["accounts"]] == ["google", "microsoft"]
        assert "accounts_redo" not in data["answers"]


def test_a_notetaker_key_is_validated_before_any_vault_exists(tmp_path):
    """The real provider module, in a fresh process with an empty state dir
    (no vault pointer, no config): the key check must reach the provider's
    HTTP call. It once died importing the notetaker layer, which read the
    config at import, so every key on a new machine read as "did not work"."""
    import subprocess
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(sf.APP_DIR)!r})\n"
        "import requests\n"
        "calls = []\n"
        "class R:\n"
        "    text = ''\n"
        "    def __init__(self, code): self.status_code, self.ok = code, code == 200\n"
        "    def json(self): return {'notes': []}\n"
        "def get(url, headers=None, params=None, timeout=None):\n"
        "    calls.append(url)\n"
        "    return R(200 if (headers or {}).get('Authorization') == 'Bearer good-key' else 401)\n"
        "requests.get = get\n"
        "import setup_flow as sf\n"
        "print(json.dumps({'good': sf.validate_notetaker_key('granola', 'good-key'),\n"
        "                  'bad': sf.validate_notetaker_key('granola', 'bad-key'),\n"
        "                  'help': sf._key_help('granola'), 'calls': len(calls)}))\n")
    env = dict(os.environ)
    env["VAN_GOGH_STATE_DIR"] = str(tmp_path / "empty-state")
    env.pop("GRANOLA_API_KEY", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["good"] == {"ok": True}
    assert out["bad"] == {"ok": False, "error": "NotetakerError"}
    assert out["calls"] == 2
    import notetaker_granola
    assert out["help"] == notetaker_granola.KEY_HELP, "the provider's own words, whatever they are"


def test_the_notetaker_prompt_names_the_notetakers_and_where_each_key_is(world):
    import notetaker_grain
    import notetaker_granola
    spec = sf.notetaker_prompt_spec()
    assert spec["question"] == "Which meeting notetaker do you use?"
    assert [o["label"] for o in spec["options"]] == ["Granola", "Grain", "None, or another notetaker"]
    assert spec["options"][0]["description"] == "You paste its API key next. " + notetaker_granola.KEY_HELP
    assert spec["options"][1]["description"] == "You paste its API key next. " + notetaker_grain.KEY_HELP
    assert spec["options"][2]["description"] == (
        "Van Gogh reads Granola and Grain today. Briefings still work, without meeting notes.")
    assert "key" not in " ".join(o["label"] for o in spec["options"]).lower()
    prepare(world)
    out = sf.run_next(None)
    out = sf.run_next({"id": "notetaker", "value": {"provider": "Granola", "key": "wrong"}})
    assert out["prompt"]["question"].startswith("That Granola key did not work. Paste it again")
    out = sf.run_next({"id": "notetaker", "value": {"provider": "Granola", "key": NOTETAKER_KEY}})
    assert out["prompt"]["id"] == "confirm"
    assert out["prompt"]["question"].splitlines() == [
        "Name: Jane Doe (Jane)", "Company: Acme", "Timezone: America/New_York",
        f"Notes folder: {world.vault} (where Van Gogh keeps its pages)",
        "Accounts: jane@acme.example, jane@corp.example", "Does this look right?"]


def test_the_deals_and_priorities_questions_say_how_ticking_works(world):
    seen = {}
    world.research_state = "running"
    prepare(world)
    world.drive(on_prompt=lambda o: seen.setdefault(o["prompt"]["id"], o["prompt"]))
    assert seen["deals"]["question"] == (
        "Which of these are real deals? Nothing is ticked to start. Tick each one that is a "
        "real, live deal; only ticked ones are tracked.")
    assert seen["priorities"]["question"] == (
        "What matters most right now in Acme? Tick any that are right, and type your own if "
        "they are missing. This is what ranks your briefings.")


def test_cold_read_md_is_the_render_and_names_nobody_real():
    """cold_read.md ships in the public mirror: its text is the renderer's
    output, byte for byte, and carries only the invented client."""
    import importlib.util
    path = sf.PLUGIN_ROOT / "objectives" / "fifteen-minute-install" / "cold_read_plant.py"
    spec = importlib.util.spec_from_file_location("cold_read_plant", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    before = (os.environ.get("VAN_GOGH_STATE_DIR"), sf.NOW)
    text = mod.render()
    assert (os.environ.get("VAN_GOGH_STATE_DIR"), sf.NOW) == before, "the render leaves no trace"
    md = (path.parent / "cold_read.md").read_text(encoding="utf-8")
    assert mod.rendered_block(md) == text, "run cold_read_plant.py --write-md"
    import re as _re
    home = Path.home()
    for real in (str(home), "jane@acme.example"):
        assert real not in text and real not in md, real
    if home.name not in ("jane", "runner", "root"):
        assert home.name not in text and home.name not in md, "a real user name in the render"
    assert set(_re.findall(r"/Users/([A-Za-z0-9_.]+)", text + md)) <= {"jane"}
    for invented in ("Jane Doe", "Acme Energy", "jane@acme-energy.example",
                     "/Users/jane/van-gogh-vault", "team@example.com"):
        assert invented in text, invented
    assert mod.SETUP_LINE_MAC in text and mod.SETUP_LINE_WINDOWS in text
    error_lines = [l[2:] for l in text.split("# Error lines")[1].split("# Closing text")[0].splitlines()
                   if l.startswith("- ")]
    assert len(error_lines) == 34 and max(len(l) for l in error_lines) <= 120


def test_every_error_line_names_an_action_before_the_command_and_both_platforms():
    """A line ending "Then type X" with nothing to do first is unactionable;
    "Terminal" alone is Mac-only wording."""
    import re
    src = (sf.APP_DIR / "setup_flow.py").read_text(encoding="utf-8")
    assert "Then type" not in src
    for line in sf.all_error_lines("team@example.com"):
        if "Terminal" in line:
            assert "Terminal (PowerShell on Windows)" in line, "Mac-only wording: " + line
    for text in ("The vault could not be scaffolded.", "something failed"):
        line = sf._hint_line(text)
        assert line.endswith(f"To retry, in Claude, type {sf.RESUME}") and len(line) <= 120


def test_no_key_file_stops_with_a_plain_line_and_prepare_still_exits_zero(world):
    world.key_file.unlink()
    pre = prepare(world)
    assert pre["ok"] is True and "credentials" in pre["open"]
    assert pre["keyfile"] is False and pre["keyfile_error"] == "no key file"
    out = sf.run_next(None)
    assert out["kind"] == "error" and out["box"] == "credentials"
    assert "key file" in out["line"] and sf.RESUME in out["line"] and len(out["line"]) <= 120


def test_confirm_can_change_a_field_and_the_timezone_is_never_applied_silently(world):
    prepare(world)
    out = sf.run_next(None)
    out = sf.run_next({"id": "notetaker", "value": "None, or another notetaker"})
    assert out["prompt"]["id"] == "confirm"
    assert out["prompt"]["fields"]["timezone"] == "America/New_York"
    assert "America/New_York" in out["prompt"]["question"]
    out = sf.run_next({"id": "confirm", "value": {"timezone": "America/Chicago",
                                                   "vault_path": str(world.home / "brain")}})
    assert out["kind"] == "prompt" and out["prompt"]["id"] == "confirm"
    assert out["prompt"]["fields"]["timezone"] == "America/Chicago"
    world.vault = world.home / "brain"
    out = sf.run_next({"id": "confirm", "value": "Yes, continue"})
    assert out["prompt"]["id"] == "schedule"
    cfg = json.loads((world.home / "brain" / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert cfg["user"]["timezone"] == "America/Chicago"
    assert cfg["user"]["full_name"] == "Jane Doe"
    assert cfg["obsidian"]["hotcache_action_items_heading"] == "Jane's Action Items"
    assert cfg["support"]["team_email"] == "team@vangogh.example"
    assert [b["tag"] for b in cfg["businesses"]] == ["acme", "personal"]
    assert cfg["accounts"][0]["is_primary"] and cfg["accounts"][0]["label"] == "Acme"
    assert "Documents" not in str(world.vault)


# ── sign-in with no browser on this computer (the by-link prompt) ────────────

def test_no_browser_signs_in_by_link_for_google_and_by_code_for_microsoft(world):
    """Not an error: the client is shown a link (Google) or a short code
    (Microsoft) to finish from any device. A sixth and seventh prompt, never
    part of the happy path."""
    world.browserless = {"google", "microsoft"}
    seen = []
    prepare(world)
    out, prompts, outs = world.drive(on_prompt=lambda o: seen.append(o["prompt"]))
    assert out["kind"] == "done" and out["complete"], out
    assert prompts == ["signin_link", "signin_link"] + list(sf.PROMPT_IDS)
    google, microsoft = seen[0], seen[1]
    assert "https://accounts.example/o/oauth2/auth?state=s1" in google["question"]
    assert "looks like an error" in google["question"] and "That is expected" in google["question"]
    assert "any device" not in google["question"], "a phone cannot easily hand the address back"
    assert "One on this computer is easiest" in google["question"]
    assert google["question"].endswith('4. Choose "I signed in"; you paste the address next.')
    assert "paste it here" not in google["question"]
    assert [o["label"] for o in google["options"]] == ["I signed in", "Not now"]
    assert google["options"][0]["description"] == "You paste the copied address next."
    assert "On any device" in microsoft["question"]
    assert "https://microsoft.com/devicelogin" in microsoft["question"]
    assert "ABCD-EFGH" in microsoft["question"]
    assert [o["label"] for o in microsoft["options"]] == ["Done, I signed in", "Not now"]
    assert world.redirects == ["http://localhost:8080/?state=s1&code=4/abc"]
    assert world.device_completions == 1
    assert ss.read_stamp()["report"]["facts"]["accounts"]["detail"] == "2 signed in"
    # The pasted address (it carries a one-time code) is not in the ledger or the stamp.
    for text in (ss.timing_path().read_text(encoding="utf-8"), ss.stamp_path().read_text(encoding="utf-8")):
        assert "4/abc" not in text


def test_a_working_browser_never_sees_the_link_prompt(world):
    pre, out, prompts, outs = full_run(world)
    assert "signin_link" not in prompts and prompts == list(sf.PROMPT_IDS)
    assert world.redirects == [] and world.device_completions == 0


def test_a_reported_no_browser_starts_the_link_flow_instead_of_stopping(world):
    world.no_browser_class = {"google"}
    prepare(world)
    out = sf.run_next(None)
    assert out["kind"] == "prompt" and out["prompt"]["id"] == "signin_link"
    flags = [c[1] for c in world.calls if c[0] == "auth_bootstrap.py"]
    assert "--headless" not in flags[0] and "--headless" in flags[1]
    # Tapped without pasting: asked again, with the reason leading the question.
    out = sf.run_next({"id": "signin_link", "value": "I signed in"})
    assert out["prompt"]["id"] == "signin_link"
    assert out["prompt"]["question"].startswith(
        "That was not a web address. Paste the full address of the page you ended on.\n")
    out = sf.run_next({"id": "signin_link", "value": {"label": "I signed in",
                                                      "address": "http://localhost:8080/?code=9"}})
    assert out["kind"] == "prompt" and out["prompt"]["id"] == "notetaker"
    assert world.redirects == ["http://localhost:8080/?code=9"]


def test_not_now_stops_with_a_line_and_the_resume_asks_again(world):
    world.browserless = {"google"}
    prepare(world)
    out = sf.run_next(None)
    out = sf.run_next({"id": "signin_link", "value": "Not now"})
    assert out["kind"] == "error" and out["line"] == (
        "Sign-in is not finished. To come back to it, in Claude, type /van-gogh:install-van-gogh")
    out = sf.run_next(None)
    assert out["kind"] == "prompt" and out["prompt"]["id"] == "signin_link"


def test_when_the_link_flow_cannot_start_the_line_says_who_to_write_to(world):
    world.no_browser_class = {"google"}
    world.headless_broken = True
    prepare(world)
    out = sf.run_next(None)
    assert out["kind"] == "error" and out["box"] == "accounts"
    assert out["line"] == ("The sign-in link could not be made on this computer. Write to "
                           "team@vangogh.example and we will sign you in another way.")
    assert len(out["line"]) <= 120


def test_a_missing_credential_is_installed_from_downloads_and_the_sign_in_retried(world, monkeypatch):
    """The line says "save the key file into your Downloads folder", so a key
    file found there is installed and the sign-in tried again, by `next`."""
    prepare(world)
    installs = []
    monkeypatch.setattr(sf, "_install_keyfile", lambda data: installs.append(1) or {"ok": len(installs) > 1})
    world.auth_fail["google"] = "no_credential"
    out = sf.run_next(None)
    assert out["kind"] == "error" and out["line"] == (
        "Save the key file we sent you into your Downloads folder, then in Claude, type "
        "/van-gogh:install-van-gogh")
    assert installs == [1]
    # The file is there now: the next `next` installs it and signs in.
    real = world.run_script

    def run(name, args, timeout=600, input_text=None, cwd=None):
        if name == "auth_bootstrap.py" and len(installs) > 1:
            world.auth_fail.pop("google", None)
        return real(name, args, timeout, input_text, cwd)

    monkeypatch.setattr(sf, "_run_script_raw", run)
    out = sf.run_next(None)
    assert installs == [1, 1]
    assert out["kind"] == "prompt" and out["prompt"]["id"] == "notetaker"


def test_a_key_file_saved_after_the_error_is_installed_by_next(world):
    """With the credentials box open, `next` itself discovers and installs a
    key file from Downloads; nothing else has to be run."""
    saved = world.key_file.read_text(encoding="utf-8")
    world.key_file.unlink()
    prepare(world)
    out = sf.run_next(None)
    assert out["kind"] == "error" and out["box"] == "credentials"
    assert out["line"] == ("Your key file is not in Downloads. Save it there, then in Claude, "
                           "type /van-gogh:install-van-gogh")
    world.key_file.write_text(saved, encoding="utf-8")
    out = sf.run_next(None)
    assert out["kind"] == "prompt" and out["prompt"]["id"] == "notetaker"
    assert (world.state / "keyfile" / "van-gogh-key.json").exists() and not world.key_file.exists()
    assert ss.box(ss.load(), "credentials")["status"] == ss.DONE


# ── timezone: what a person types ────────────────────────────────────────────

@pytest.mark.parametrize("typed,zone", [
    ("America/Chicago", "America/Chicago"), ("america/chicago", "America/Chicago"),
    ("Chicago", "America/Chicago"), ("chicago", "America/Chicago"),
    ("New York", "America/New_York"), ("new   york", "America/New_York"),
    ("los angeles", "America/Los_Angeles"), ("London", "Europe/London"),
    ("Singapore", "Asia/Singapore"),
    ("ET", "America/New_York"), ("EST", "America/New_York"), ("edt", "America/New_York"),
    ("Eastern", "America/New_York"), ("CT", "America/Chicago"), ("Central", "America/Chicago"),
    ("MT", "America/Denver"), ("Mountain", "America/Denver"),
    ("PT", "America/Los_Angeles"), ("Pacific", "America/Los_Angeles"),
    ("GMT", "Etc/GMT"), ("UTC", "UTC"),
    ("Springfield", ""), ("", ""), ("Austin, Texas", ""),
])
def test_resolve_timezone(typed, zone):
    assert sf.resolve_timezone(typed) == zone


def test_a_typed_city_is_resolved_shown_for_a_tap_and_an_unknown_one_reasked(world):
    prepare(world)
    sf.run_next(None)
    out = sf.run_next({"id": "notetaker", "value": "None, or another notetaker"})
    out = sf.run_next({"id": "confirm", "value": {"timezone": "Chicago"}})
    assert out["prompt"]["id"] == "confirm"
    assert out["prompt"]["question"].startswith("Updated.\n")
    assert "Timezone: America/Chicago" in out["prompt"]["question"]
    assert not ss.load()["answers"].get("confirmed"), "shown for a tap, never applied silently"
    out = sf.run_next({"id": "confirm", "value": {"timezone": "Springfield"}})
    assert out["prompt"]["question"].startswith(
        "I could not place Springfield. Type the largest city near you that shares your clock, "
        "for example Chicago, London or Singapore.\n")
    assert "Timezone: America/Chicago" in out["prompt"]["question"], "the last good answer stands"
    out = sf.run_next({"id": "confirm", "value": "Yes, continue"})
    assert out["prompt"]["id"] == "schedule"
    cfg = json.loads((world.vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert cfg["user"]["timezone"] == "America/Chicago"


def test_details_the_key_file_did_not_carry_are_asked_for_first():
    empty = {"full_name": "", "first_name": "", "company": "", "timezone": ""}
    assert sf._ask_reason(empty) == (
        "First, tell me your full name, your company and your timezone. "
        "For your timezone, the city you live in is enough.")
    assert sf._ask_reason({**empty, "full_name": "Jane Doe", "first_name": "Jane",
                           "timezone": "America/New_York"}) == (
        "First, tell me your company.")
    assert sf._ask_reason({"full_name": "Jane Doe", "first_name": "Jane", "company": "Acme",
                           "timezone": "America/New_York"}) == ""


def test_missing_details_are_asked_for_in_the_singular_or_the_plural():
    assert sf._missing_reason(["timezone"]) == "Still missing: your timezone. What is it?"
    assert sf._missing_reason(["first_name", "timezone"]) == (
        "Still missing: your first name and your timezone. What are they?")
    assert sf._missing_reason(["full_name", "first_name", "timezone"]) == (
        "Still missing: your full name, your first name and your timezone. What are they?")


# ── deals: plain labels, the index kept inside ───────────────────────────────

def test_deal_options_are_the_heading_alone_and_duplicates_carry_the_person(world):
    deals = [(0, {"heading": "Solar PPA", "name": "Dana Reyes", "stage": "negotiating",
                  "last_contact": "2026-09-28"}),
             (3, {"heading": "Solar PPA", "name": "Lee Park", "stage": "proposal",
                  "last_contact": "2026-09-05"}),
             (4, {"heading": "Storage lease", "name": "", "email": "sam@x.example",
                  "stage": "cold", "last_contact": "not a date"})]
    spec = sf.deals_prompt_spec(deals)
    assert [o["label"] for o in spec["options"]] == [
        "Solar PPA (Dana Reyes)", "Solar PPA (Lee Park)", "Storage lease", "None of these"]
    assert spec["options"][0]["description"] == "Dana Reyes, negotiating, last contact September 28"
    assert spec["options"][1]["description"] == "Lee Park, proposal, last contact September 5"
    assert spec["options"][2]["description"] == "sam@x.example, cold, last contact not a date"
    assert spec["options"][3]["description"] == "Pick this alone when none are real deals."
    assert dict(sf.deal_labels(deals)) == {"Solar PPA (Dana Reyes)": 0, "Solar PPA (Lee Park)": 3,
                                           "Storage lease": 4}
    # Through the flow: a tick comes back as a label and lands as the index.
    seen = {}
    prepare(world)
    out, prompts, outs = world.drive(on_prompt=lambda o: seen.setdefault(o["prompt"]["id"], o["prompt"]))
    assert out["complete"]
    labels = [o["label"] for o in seen["deals"]["options"]]
    assert labels == ["Harbor Point solar PPA", "Northwind storage lease", "Old newsletter thread",
                      "None of these"]
    assert not any(l.startswith("[") or "|" in l for l in labels)
    apply_call = next(c for c in world.calls if c[0] == "warm_start.py" and c[1][0] == "apply")
    assert apply_call[1][apply_call[1].index("--deals") + 1] == "0,1"
    assert ss.read_stamp()["answers"]["declined_deals"] == [2]


def test_the_usage_limit_text_promises_only_what_is_installed(world):
    """"They file by themselves tonight" is said only when the nightly jobs
    are installed; a client who declined them is told what to type."""
    world.cap_at = "summaries"
    world.schedule_answer = "Schedule nothing"
    prepare(world)
    out, prompts, outs = world.drive()
    text = out["closing_text"]
    assert text.startswith(
        "Setup is almost done: 6 of your 10 meetings are filed. The other 4 are waiting because "
        "your Claude plan's usage limit was reached. To file them once the limit resets "
        "(Claude shows the time when you type there), "
        "in Claude, type /van-gogh:install-van-gogh\n")
    assert "tonight" not in text and "overnight" not in text
    data = ss.new("r")
    for key in ss.KEYS:
        ss.set_status(data, key, ss.DONE)
    data["degraded"] = {"box": "meetings", "filed": 9, "waiting": 1}
    ss.set_status(data, "meetings", ss.PENDING)
    assert "The other one is waiting" in sf.unfinished_text(data)
    assert "it files by itself overnight while this computer is on." in sf.unfinished_text(data)
    assert "tonight" not in sf.unfinished_text(data)
    for box, words in (("deals", "Reading your mail stopped"), ("briefings", "first briefings are waiting")):
        data["degraded"] = {"box": box}
        text = sf.unfinished_text(data)
        assert words in text and "your Claude plan's usage limit was reached" in text
        assert text.count("/van-gogh:") == 1 and text.endswith("in Claude, type /van-gogh:install-van-gogh")


# ── a meeting too long to read during the install ────────────────────────────

def test_a_long_meeting_filed_from_the_summary_is_said_once_and_truthfully(world):
    world.summary_only = 1
    pre, out, prompts, outs = full_run(world)
    assert out["complete"]
    assert out["report"]["facts"]  # the run finished normally
    text = out["closing_text"]
    assert ("1 long meeting was filed from your notetaker's own summary for now; Van Gogh reads "
            "the full recording overnight while this computer is on.") in text
    assert text.count("notetaker's own summary") == 1 and "/van-gogh:meeting-ingest" not in text


def test_long_meetings_with_no_jobs_name_the_command_that_reads_them(world):
    world.summary_only = 2
    world.schedule_answer = "Schedule nothing"
    seen = []
    prepare(world)
    out, prompts, outs = world.drive(on_prompt=lambda o: seen.append(ss.load()))
    assert out["complete"]
    text = out["closing_text"]
    assert ("2 long meetings were filed from your notetaker's own summary for now. To have the "
            "full recordings read, in Claude, type /van-gogh:meeting-ingest") in text
    assert "tonight" not in text


def test_the_meetings_box_records_how_many_came_from_the_summary(world):
    assert sf.meetings_detail(10, 0, 1) == "10 filed, 1 from the notetaker's summary"
    assert sf.meetings_detail(10) == "10 filed"
    assert sf.summary_only_line(0, True) == "" and sf.summary_only_line(0, False) == ""
    world.summary_only = 1
    prepare(world)
    details = []
    real_finish = sf.finish

    def spy(data=None):
        details.append(ss.box(data, "meetings")["detail"])
        return real_finish(data)

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(sf, "finish", spy)
        world.drive()
    assert details[-1] == "10 filed, 1 from the notetaker's summary"
    # A reply with no `summary_only` key at all (before the pipeline lane lands it) is none.
    assert sf._count(None) == 0


def test_a_meeting_with_no_transcript_is_never_promised_a_full_read():
    """`summary_only` also lists meetings the notetaker has no transcript for.
    Only a budget cut is queued for a later read, so only those are counted."""
    rows = [{"id": "a", "over_budget": True}, {"id": "b", "over_budget": False},
            {"id": "c"}, "junk"]
    assert sf._count_over_budget(rows) == 1
    assert sf._count_over_budget(None) == 0
    assert sf._count_over_budget(3) == 0


def test_the_limit_says_when_it_resets_when_its_own_message_named_a_time(world):
    """"Once the limit resets" with no way to know when is no instruction.
    The time comes from the cap's own message (failure_class.quota_reset_at);
    when it named none, the line says where the time is shown."""
    assert sf._cap_reset({"text": "You're out of extra usage, resets 3:40pm"}) == "3:40 PM"
    assert sf._cap_reset({"text": "Claude AI usage limit reached, resets at 9am"}) == "9:00 AM"
    assert sf._cap_reset({"text": "Claude usage limit reached"}) == ""
    world.cap_at = "scan"            # the stub's scan cap message says "resets 3:40pm"
    prepare(world)
    out, prompts, outs = world.drive()
    assert ss.load()["degraded"]["reset"] == "3:40 PM"
    assert out["closing_text"].startswith(
        "Setup is almost done. Reading your mail stopped because your Claude plan's usage limit "
        "was reached. To pick up where it stopped once the limit resets at 3:40 PM, in Claude, "
        "type /van-gogh:install-van-gogh\n")
    data = ss.new("r")
    for key in ss.KEYS:
        ss.set_status(data, key, ss.DONE)
    ss.set_status(data, "briefings", ss.PENDING)
    data["degraded"] = {"box": "briefings", "reset": ""}
    assert sf.unfinished_text(data).endswith(
        "To have them written once the limit resets (Claude shows the time when you type there), "
        "in Claude, type /van-gogh:install-van-gogh")


def test_the_key_file_line_says_to_save_it_in_downloads():
    line = sf.error_line("no_credentials")
    assert line == ("No key file yet. Ask the Van Gogh team for it, save it in Downloads, then "
                    "in Claude, type /van-gogh:install-van-gogh") and len(line) <= 120


# ── accounts come from the state .env when the box was ticked from disk ──────

def _plant_tokens(world, with_email=True):
    env = world.state / ".env"
    text = ("GOOGLE_REFRESH_TOKEN_PERSONAL=tok-personal-SECRET\n"
            "MS_GRAPH_REFRESH_TOKEN_NORTHWIND=tok-northwind-SECRET\n")
    if with_email:
        text += "GOOGLE_ACCOUNT_EMAIL_PERSONAL=jane@gmail.example\n"
    env.write_text(text, encoding="utf-8")


def test_a_ticked_accounts_box_never_writes_a_placeholder_account(world, monkeypatch):
    """The real run: tokens already in .env (PERSONAL, NORTHWIND), so the
    accounts box ticks from disk and the sign-in step never runs. The config
    must carry THOSE accounts, labelled so the scan finds each token, never
    the template's you@gmail.com / Gmail."""
    import google_client
    _plant_tokens(world, with_email=True)
    world.live_addresses = {"Northwind": "jane@northwind.example"}
    pre = prepare(world)
    assert "accounts" not in pre["open"], "ticked from disk before any sign-in"
    seen = {}
    out, prompts, outs = world.drive(on_prompt=lambda o: seen.setdefault(o["prompt"]["id"], o["prompt"]))
    assert out["complete"]
    assert not [c for c in world.calls if c[0] == "auth_bootstrap.py"], "no sign-in ran"
    assert "Accounts: jane@gmail.example, jane@northwind.example" in seen["confirm"]["question"]
    cfg = json.loads((world.vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert [(a["provider"], a["label"], a["email"], a["is_primary"]) for a in cfg["accounts"]] == [
        ("google", "Personal", "jane@gmail.example", True),
        ("microsoft", "Northwind", "jane@northwind.example", False)]
    for a in cfg["accounts"]:
        assert a["email"] not in sf.PLACEHOLDER_EMAILS
    # week_review's own lookup finds a token for each label.
    for line in (world.state / ".env").read_text(encoding="utf-8").splitlines():
        k, v = line.split("=", 1)
        monkeypatch.setenv(k, v)
    assert google_client._refresh_token_for("Personal") == "tok-personal-SECRET"
    import auth_bootstrap
    for label in ("Personal", "Northwind", "Northwind 2", "QX"):
        assert sf.label_suffix(label) == auth_bootstrap._label_suffix(label)
    assert sf._label_for_suffix("NORTHWIND_2") == "Northwind 2"
    # The live call was made only for the account with no stored address.
    smoke = [c for c in world.calls if c[0] == "microsoft_client.py"]
    assert smoke and smoke[0][1] == ["--label", "Northwind"]


def test_an_account_with_no_address_shows_unknown_address_on_the_confirm_screen(world):
    _plant_tokens(world, with_email=False)
    world.live_addresses = {}
    prepare(world)
    seen = {}
    out, prompts, outs = world.drive(on_prompt=lambda o: seen.setdefault(o["prompt"]["id"], o["prompt"]))
    assert out["complete"]
    assert "Accounts: unknown address (Personal), unknown address (Northwind)" in seen["confirm"]["question"]
    cfg = json.loads((world.vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert [a["label"] for a in cfg["accounts"]] == ["Personal", "Northwind"]
    assert all(a["email"] == "" for a in cfg["accounts"])


def test_the_config_writer_refuses_a_placeholder_or_an_unmatched_label(world):
    prepare(world)
    sf.run_next(None)
    sf.run_next({"id": "notetaker", "value": "None, or another notetaker"})
    data = ss.load()
    data["answers"]["accounts"] = [{"provider": "google", "email": "jane@acme.example",
                                    "label": "Gmail", "env_var": "", "slot": ""}]
    ss.save(data)
    out = sf.run_next({"id": "confirm", "value": "Yes, continue"})
    assert out["kind"] == "error" and out["box"] == "vault"
    assert out["line"] == ("Your accounts could not be written to the settings. "
                           "To sign in again, in Claude, type /van-gogh:install-van-gogh")
    assert len(out["line"]) <= 120
    data = ss.load()
    data["answers"]["accounts"] = [{"provider": "google", "email": "you@gmail.com",
                                    "label": "Acme", "env_var": "", "slot": ""}]
    ss.save(data)
    out = sf.run_next(None)
    assert out["kind"] == "error" and "could not be written" in out["line"]


# ── research: the confirmed company, or nothing ──────────────────────────────

def _research_starts(world):
    return [c[1] for c in world.calls if c[0] == "operator_research.py" and c[1][:1] == ["start"]]


def test_research_starts_with_the_confirmed_company_not_the_key_files_blank(world):
    key = json.loads((world.key_file).read_text(encoding="utf-8"))
    key["client"]["company"] = ""
    key["client"]["role"] = "CEO"
    key["client"]["city"] = "Austin"
    world.key_file.write_text(json.dumps(key), encoding="utf-8")
    prepare(world)
    sf.run_next(None)
    sf.run_next({"id": "notetaker", "value": "None, or another notetaker"})
    out = sf.run_next({"id": "confirm", "value": {"company": "Northwind Energy"}})
    assert "Company: Northwind Energy" in out["prompt"]["question"]
    out = sf.run_next({"id": "confirm", "value": "Yes, continue"})
    assert out["prompt"]["id"] == "schedule"
    starts = _research_starts(world)
    assert len(starts) == 1
    argv = starts[0]
    assert argv[argv.index("--company") + 1] == "Northwind Energy"
    assert argv[argv.index("--role") + 1] == "CEO" and argv[argv.index("--city") + 1] == "Austin"


def test_no_company_means_no_research_and_generic_priorities(world):
    key = json.loads((world.key_file).read_text(encoding="utf-8"))
    key["client"]["company"] = ""
    world.key_file.write_text(json.dumps(key), encoding="utf-8")
    world.email_for = {"google": "jane@gmail.example", "microsoft": "jane@outlook.example"}
    seen = {}
    prepare(world)
    out, prompts, outs = world.drive(on_prompt=lambda o: seen.setdefault(o["prompt"]["id"], o["prompt"]))
    assert out["complete"]
    assert "Company: not set" in seen["confirm"]["question"]
    assert _research_starts(world) == [], "nothing to read up on"
    assert not [c for c in world.calls if c[0] == "operator_research.py"], "status never asked either"
    assert [o["label"] for o in seen["priorities"]["options"]] == [l for l, _ in sf.GENERIC_PRIORITIES]
    assert any(r.get("detail") == "research skipped: no company" for r in ss.read_ledger())


# ── the scan error line carries its reason ───────────────────────────────────

def test_a_scan_that_died_says_why_and_logs_the_whole_reason(world):
    world.scan_error = "no token for the Gmail account"
    prepare(world)
    out, prompts, outs = world.drive()
    assert out["kind"] == "error" and out["box"] == "deals"
    assert out["line"] == ("The mail scan stopped (no token for the Gmail account). "
                           "To run it again, in Claude, type /van-gogh:install-van-gogh")
    assert len(out["line"]) <= 120
    assert any(r.get("event") == "error" and r.get("detail") == "scan: no token for the Gmail account"
               for r in ss.read_ledger())
    long = "the scan exited without writing its snapshot because the classifier was unavailable for the whole run"
    assert sf.scan_line(long) == sf.error_line("scan"), "a reason that will not fit is left to the ledger"
    assert sf.scan_line("") == "The mail scan stopped early. To run it again, in Claude, type /van-gogh:install-van-gogh"


# ── propose before the mail review has written its results ──────────────────

def test_propose_before_the_results_are_written_waits_on_the_scan_and_tries_again(world):
    world.propose_not_written = 2
    waits = []
    prepare(world)
    out = sf.run_next(None)
    while out["kind"] != "done":
        if out["kind"] == "wait":
            waits.append(out)
            world.tick(out["poll_seconds"])
            out = sf.run_next(None)
        elif out["kind"] == "prompt":
            world.tick(5)
            out = sf.run_next({"id": out["prompt"]["id"], "value": world.answer_for(out["prompt"])})
        elif out["kind"] == "dispatch":
            world.write_briefings(out["agents"])
            out = sf.run_next(None)
        else:
            raise AssertionError(out)
    assert out["complete"]
    proposes = [c for c in world.calls if c[0] == "warm_start.py" and c[1] == ["propose"]]
    assert len(proposes) == 3, "two early replies, then the real one"
    scan_waits = [w for w in waits if w["on"] == "scan"]
    assert len(scan_waits) >= 2 and all(w["say"] == sf.SAY_SCAN for w in scan_waits)
    assert not any(r.get("event") == "error" for r in ss.read_ledger()), "never logged as an error"
    assert ss.read_stamp()["boxes"]["deals"] == ss.DONE


def test_any_other_propose_failure_names_its_reason_and_logs_it(world):
    world.propose_error = "config unreadable: JSONDecodeError"
    prepare(world)
    out, prompts, outs = world.drive()
    assert out["kind"] == "error" and out["box"] == "deals"
    assert out["line"] == ("Deals could not be read (config unreadable: JSONDecodeError). "
                           "To retry, in Claude, type /van-gogh:install-van-gogh")
    assert len(out["line"]) <= 120
    assert any(r.get("event") == "error" and r.get("detail") == "propose: config unreadable: JSONDecodeError"
               for r in ss.read_ledger())
    long = "the proposal step raised an unexpected exception while reading the week review sidecar file"
    assert sf.propose_line(long) == sf.error_line("propose")
    assert sf.propose_line("") == sf.error_line("propose")


# ── the deals witness is the picks themselves, never the refilled top list ───

def test_ticking_a_subset_is_done_when_every_pick_landed(world):
    """The live defect: 3 of 15 ticked, verify re-proposed a fresh top list,
    and "new_deals minus declined" could never reach zero."""
    seen = {}
    prepare(world)
    out, prompts, outs = world.drive(on_prompt=lambda o: seen.setdefault(o["prompt"]["id"], o["prompt"]))
    assert out["complete"]
    verify = [c for c in world.calls if c[0] == "warm_start.py" and c[1][0] == "verify"]
    assert verify[-1][1][:3] == ["verify", "--picked", "0,1"] and "--proposal" in verify[-1][1]
    data_answers = ss.read_stamp()["answers"]
    assert data_answers["declined_deals"] == [2]
    assert ss.read_stamp()["boxes"]["deals"] == ss.DONE
    # new_deals was non-zero (the untouched newsletter thread) and did not matter.
    assert world._headings() == ["Northwind storage lease", "Harbor Point solar PPA"]
    assert [d for d in PROPOSED_DEALS if d["heading"] not in world._headings()], "new_deals > 0"
    assert "not saved" not in out["closing_text"]


def test_a_pick_already_tracked_under_another_heading_is_counted_and_named(world):
    hot = world.vault / "wiki" / "hotcache.md"
    world.duplicate_of = {"Harbor Point solar PPA": "Harbor Point PPA"}
    real = world.run_script

    def run(name, args, timeout=600, input_text=None, cwd=None):
        out = real(name, args, timeout, input_text, cwd)
        if name == "vault/scaffold.py":
            text = hot.read_text(encoding="utf-8")
            hot.write_text(text.replace("## Active Threads\n",
                                        "## Active Threads\n\n### Harbor Point PPA\n- stage: live\n\n"
                                        "### Thread Name\n- stage: sample\n", 1), encoding="utf-8")
        return out

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(sf, "_run_script_raw", run)
        prepare(world)
        details = []
        real_finish = sf.finish

        def spy(data=None):
            details.append(ss.box(data, "deals")["detail"])
            return real_finish(data)

        mp.setattr(sf, "finish", spy)
        out, prompts, outs = world.drive()
    assert out["complete"]
    assert details[-1] == "2 deals tracked (1 already tracked as Harbor Point PPA)"
    assert "Thread Name" not in details[-1] and "Thread Name" not in hot.read_text(encoding="utf-8")
    assert ss.read_stamp()["boxes"]["deals"] == ss.DONE


def test_a_pick_that_did_not_land_is_the_not_saved_error_with_its_count(world):
    real = world.run_script

    def run(name, args, timeout=600, input_text=None, cwd=None):
        out = real(name, args, timeout, input_text, cwd)
        if name == "warm_start.py" and args[0] == "verify" and "--picked" in args:
            out["json"]["missing"] = out["json"]["picked"][:1]
            out["json"]["landed"] = out["json"]["picked"][1:]
            out["text"] = json.dumps(out["json"])
        return out

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(sf, "_run_script_raw", run)
        prepare(world)
        out, prompts, outs = world.drive()
    assert out["kind"] == "error" and out["box"] == "deals"
    assert out["line"] == ("1 ticked deal was not saved. To retry, in Claude, type "
                           "/van-gogh:install-van-gogh")
    assert sf.outstanding_line(2).startswith("2 ticked deals were not saved.")
    assert any(r.get("event") == "error" and r.get("detail", "").startswith("not saved: [0]")
               for r in ss.read_ledger())


# ── a proposal staged yesterday, applied today ───────────────────────────────

def _next_day_at_the_deals_question(world):
    def on_prompt(o):
        if o["prompt"]["id"] == "deals":
            world.tick(24 * 3600)      # the client answers tomorrow
    return on_prompt


def test_picks_made_yesterday_are_carried_onto_todays_proposal_by_heading(world):
    """The live defect: apply looked for yesterday's dated proposal folder
    and the client was stuck at the very point the reminder sends them to."""
    prepare(world)
    out, prompts, outs = world.drive(on_prompt=_next_day_at_the_deals_question(world))
    assert out["complete"] and prompts == list(sf.PROMPT_IDS), prompts
    ws = [c[1] for c in world.calls if c[0] == "warm_start.py"]
    proposes = [a for a in ws if a[0] == "propose"]
    applies = [a for a in ws if a[0] == "apply"]
    assert len(proposes) == 2, "proposed again when apply said need_propose"
    assert len(applies) == 2 and applies[0][-2:] != applies[1][-2:], "the second apply names today's proposal"
    assert "--proposal" in applies[1] and "--proposal" in [a for a in ws if a[0] == "verify"][-1]
    assert world._headings() == ["Northwind storage lease", "Harbor Point solar PPA"]
    stamp = ss.read_stamp()
    assert stamp["boxes"]["deals"] == ss.DONE
    assert stamp["answers"]["declined_headings"] == ["Old newsletter thread"]
    assert stamp["answers"]["declined_deals"] == [2]
    assert any("re-proposed: 2 of 2 picks found" in str(r.get("detail")) for r in ss.read_ledger())


def test_a_picked_heading_gone_from_todays_proposal_asks_the_question_again(world):
    world.vanish_after_day_change = {"Northwind storage lease"}
    seen = []
    on_day = _next_day_at_the_deals_question(world)

    def on_prompt(o):
        seen.append(o["prompt"])
        if o["prompt"]["id"] == "deals" and len([p for p in seen if p["id"] == "deals"]) == 1:
            on_day(o)

    prepare(world)
    out, prompts, outs = world.drive(on_prompt=on_prompt)
    assert out["complete"]
    assert prompts == ["notetaker", "confirm", "schedule", "priorities", "deals", "deals"], prompts
    second = [p for p in seen if p["id"] == "deals"][1]
    labels = [o["label"] for o in second["options"]]
    assert labels == ["Harbor Point solar PPA", "None of these"], "the declined thread stays out, the gone one is gone"
    assert world._headings() == ["Harbor Point solar PPA"]
    assert ss.read_stamp()["boxes"]["deals"] == ss.DONE
    assert any("re-proposed: 1 of 2 picks found" in str(r.get("detail")) for r in ss.read_ledger())


def test_an_apply_failure_names_its_reason_and_logs_it(world):
    world.apply_error = "hotcache.md is read-only"
    prepare(world)
    out, prompts, outs = world.drive()
    assert out["kind"] == "error" and out["box"] == "deals"
    assert out["line"] == ("The ticked deals could not be saved (hotcache.md is read-only). "
                           "To retry, in Claude, type /van-gogh:install-van-gogh")
    assert len(out["line"]) <= 120
    assert any(r.get("event") == "error" and r.get("detail") == "apply: hotcache.md is read-only"
               for r in ss.read_ledger())
    long = "the hot cache could not be written because the vault folder is on a read-only volume today"
    assert sf.apply_line(long) == sf.error_line("apply") and sf.apply_line("") == sf.error_line("apply")


def test_a_briefing_names_a_seeded_deal_by_a_distinctive_word_or_the_counterparty(monkeypatch):
    """Found live: the week and morning briefings said "Waterloo" and
    "Michael Marcotte" but never the hot cache heading "Bearpeakpower:
    Waterloo", and the witness demanded the heading verbatim. A generic word
    alone must still not count."""
    monkeypatch.setattr(sf, "_seeded_counterparties",
                        lambda h: ["Michael Marcotte"] if "Bearpeak" in h else [])
    seeded = ["Bearpeakpower: Waterloo", "Lois Marie Gordon: Nobel | Chief Commercial Officer"]
    assert sf._names_seeded_deal("Nudge Michael Marcotte on the Waterloo site", seeded)
    assert sf._names_seeded_deal("Bearpeakpower came back", seeded)
    assert sf._names_seeded_deal("Bearpeakpower: Waterloo is live", seeded)
    assert not sf._names_seeded_deal("Nothing moved on any thread today", seeded)
    assert not sf._names_seeded_deal("", seeded)
    # A heading made only of generic words cannot match on a generic word.
    monkeypatch.setattr(sf, "_seeded_counterparties", lambda h: [])
    assert not sf._names_seeded_deal("the meeting about the project", ["Project: Meeting"])


# ── the answer file ──────────────────────────────────────────────────────────

def _next_cli(*argv):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sf.main(["next", *argv])
    return json.loads(buf.getvalue())


def test_every_prompt_says_where_its_answer_goes_and_the_file_is_read_once(world):
    """An answer travels in a file inside the private setup folder. The
    script reads it and deletes it in the same breath, because one of the
    answers is a notetaker key."""
    prepare(world)
    out = _next_cli()
    seen = 0
    while out["kind"] in ("prompt", "wait", "dispatch"):
        if out["kind"] == "prompt":
            seen += 1
            target = Path(out["prompt"]["answer_file"])
            assert target == ss.setup_dir() / "answer.json"
            answer = {"id": out["prompt"]["id"], "value": world.answer_for(out["prompt"])}
            target.write_text(json.dumps(answer), encoding="utf-8")
            world.tick(20)
            out = _next_cli("--answer-file", str(target))
            assert not target.exists(), f"the answer to {answer['id']} was left on disk"
        elif out["kind"] == "wait":
            world.tick(out["poll_seconds"])
            out = _next_cli()
        else:
            world.write_briefings(out["agents"])
            out = _next_cli()
    assert out["kind"] == "done" and out["complete"]
    assert seen == 5
    leftovers = [p for p in ss.setup_dir().rglob("*") if p.is_file()
                 and NOTETAKER_KEY in p.read_text(encoding="utf-8", errors="replace")]
    assert leftovers == [], "the notetaker key is in no file under the setup folder"


def test_an_answer_file_that_is_missing_or_unreadable_asks_the_same_question_again(world):
    prepare(world)
    first = _next_cli()
    assert first["kind"] == "prompt"
    target = Path(first["prompt"]["answer_file"])
    again = _next_cli("--answer-file", str(target))            # never written
    assert again["kind"] == "prompt" and again["prompt"]["id"] == first["prompt"]["id"]
    target.write_text("{ this is not json", encoding="utf-8")
    third = _next_cli("--answer-file", str(target))
    assert third["kind"] == "prompt" and third["prompt"]["id"] == first["prompt"]["id"]
    assert not target.exists(), "a file that could not be read is still deleted"


def test_a_scratch_state_folder_never_installs_into_the_real_scheduler(world, monkeypatch):
    """Same rule as the reminder job: with the state folder overridden and
    nothing forcing it, setup writes the settings and leaves the computer's
    scheduler alone, so the step stays open instead of pointing real jobs at
    a scratch vault."""
    monkeypatch.delenv("VAN_GOGH_SCHEDULER_SYNC")
    prepare(world)
    out, prompts, outs = world.drive()
    assert world.scheduler_installs == 0
    assert out["kind"] == "error" and out["box"] == "jobs"
    cfg = json.loads((world.vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert cfg["digest"]["enabled"] is True, "the settings were still written"


def test_an_install_that_already_has_a_schedule_is_shown_its_own(world, monkeypatch):
    """The fresh-install proposal is laid over a config nobody has chosen
    from yet. An install that already runs briefings, or one adopted from
    before the checklist, is shown the schedule it has: a yes must never
    switch on what its owner left off, or off what they switched on."""
    import schedule_plan
    tpl = json.loads((sf.PLUGIN_ROOT / "config.template.json").read_text(encoding="utf-8"))
    theirs, done, refused, _ = schedule_plan.apply_changes(tpl, [
        {"job": "coffee", "time": "05:45"}, {"job": "finance", "on": True}])
    assert not refused and theirs["digest"]["enabled"] is True
    monkeypatch.setattr(sf, "_read_config", lambda: json.loads(json.dumps(theirs)))
    data = ss.new("r1", "darwin", "0.84.0")
    cfg, touched = sf._schedule_proposal(data)
    on = {r["key"] for r in schedule_plan.table(cfg) if r["on"] and not r["fixed"]}
    assert on == {"digest-morning-coffee", "finance-email"}, "their two, not the twelve"
    assert cfg["digest"]["briefings"]["morning-coffee"]["time"] == "05:45"
    spec = sf.schedule_prompt_spec(data)
    assert "ON (6)" in spec["show"] and "weekdays at 5:45 AM" in spec["show"]
    # Adopted from an older install with briefings off: still theirs.
    quiet = schedule_plan.nothing(theirs)
    monkeypatch.setattr(sf, "_read_config", lambda: json.loads(json.dumps(quiet)))
    adopted = ss.new("r2", "darwin", "0.84.0")
    adopted["adopted_from"] = "pre-checklist install"
    cfg, _ = sf._schedule_proposal(adopted)
    assert not schedule_plan.any_on(schedule_plan.table(cfg))
    # And a config nobody has chosen from gets the proposal.
    fresh = ss.new("r3", "darwin", "0.84.0")
    monkeypatch.setattr(sf, "_read_config", lambda: json.loads(json.dumps(tpl)))
    cfg, _ = sf._schedule_proposal(fresh)
    assert {r["key"] for r in schedule_plan.table(cfg) if r["on"] and not r["fixed"]} == set(
        schedule_plan.SETUP_ON)


def test_a_finished_setup_introduces_vincent_once(world):
    pre, out, prompts, outs = full_run(world)
    text = out["closing_text"]
    assert text.count(sf.VINCENT_LINE) == 1
    assert text.index(sf.VINCENT_LINE) < text.index("You can close this window.")
    assert text.rstrip().endswith("You can close this window.")
