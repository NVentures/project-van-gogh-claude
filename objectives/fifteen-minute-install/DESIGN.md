# Shared design for the fifteen-minute install (build lanes read this first)

The approved plan lives at `~/.claude/plans/my-goal-is-to-noble-donut.md`; the
frozen contract at `punchlist.md` beside this file. This page fixes the
interfaces the parallel build lanes share so they can be built apart and
joined without rework. A lane that needs to change a contract here says so in
its report instead of changing it quietly.

Rules every lane follows: no em or en dash anywhere in new text (use `chr(0x2014)`
in a test fixture if one is needed), every SKILL.md command has a PowerShell
form, scripts print one JSON object and branch on it (never prose), secret
values never appear on a command line or in output, tests go under
`tests/`, the suite stays green (`.venv/bin/python -m pytest`), and every
scanner proves itself on a planted instance before its 0 is believed.

## Files and locations

- State dir `S` = `user_state.state_dir()` (`~/.config/van-gogh`, override with
  `VAN_GOGH_STATE_DIR`). Every credential lookup honours the override.
- `S/setup/checklist.json`: the install checklist (schema below). Present means
  the install is not finished. Written atomically. Holds no secret.
- `S/setup/timing.jsonl`: the timing ledger, one JSON row per event, append only.
- `S/setup/complete.json`: the completion stamp (a hint; disk is truth).
- `S/setup/run-id`: the current run id (`YYYYMMDD-HHMMSS-<4 hex>`), written by
  the setup script at clock start.
- `S/keyfile/van-gogh-key.json`: the key file after the credentials step, dir
  0700, file 0600 (Windows: owner-only ACL, assertion skipped on win32).
- `S/setup/staged/`: prefetched meeting summaries before filing.
- Logs for anything that runs with no vault: macOS `~/Library/Logs/VanGogh/`,
  Windows `%LOCALAPPDATA%\VanGogh\logs\`. Never under Documents.
- Nudge job: launchd label `com.monet.setup-nudge`, Windows task
  `VanGogh-setup-nudge`. Not a `job_registry` row on purpose (it exists before
  the vault and removes itself); documented in FOUR-CS.md prose and asserted
  absent from the registry by a test.

## The checklist (`S/setup/checklist.json`)

Nine boxes, this order, these keys:

| key | title | needs | done when (verified from disk, never from a step's claim) |
|---|---|---|---|
| plugin | Plugin installed | machine | `van-gogh` in `claude plugin list --json` (or the plugin root pointer file names an existing dir) |
| python | Python environment | machine | `S/venv` interpreter runs and imports requests, dotenv, google.oauth2, googleapiclient, msal |
| credentials | App credentials | machine | `S/google_client_secret.json` or `S/ms_client_secret.json` valid; key file in `S/keyfile/` when one was used |
| accounts | Accounts signed in | human | at least one `*_REFRESH_TOKEN_*` in `S/.env` and a live call passed this run (local check: token present and not expired; live check: once a day) |
| vault | Vault and settings | human | vault pointer resolves, `{vault}/van-gogh/config.json` parses, scaffold files present, `user.full_name` set |
| jobs | Background jobs scheduled | human | every enabled registry row is loaded (`scheduler_setup.job_states`), or the client declined jobs (skipped) |
| deals | Deals and people seeded | human | warm start `verify` reports 0 new, or the client ticked none (skipped) |
| meetings | Meetings filed | machine | N pages filed where N = min(10, available), or no notetaker (not applicable) |
| briefings | First briefings shown | machine | both first-day briefing files exist, non-empty, written this run |

```json
{
  "version": 1,
  "run_id": "20261001-170000-a1b2",
  "plugin_version": "0.81.0",
  "platform": "darwin",
  "started": "2026-10-01T17:00:00-07:00",
  "updated": "2026-10-01T17:04:10-07:00",
  "boxes": [
    {"key": "plugin", "title": "Plugin installed", "needs": "machine",
     "status": "done", "verified": "2026-10-01T17:00:40-07:00", "detail": "van-gogh 0.81.0"},
    {"key": "accounts", "title": "Accounts signed in", "needs": "human",
     "status": "open", "detail": "", "pending": null},
    {"key": "meetings", "title": "Meetings filed", "needs": "machine",
     "status": "pending", "detail": "3 of 10 summaries written", "jobs": [{"name": "prefetch", "pid": 123, "started": "..."}]}
  ],
  "answers": {"notetaker": "granola", "schedule": "all", "declined_deals": [1, 4], "skipped": ["deals"]},
  "degraded": null,
  "nudges": {"stall_started": "2026-10-02T09:00:00-07:00", "sent": [], "runs": [],
             "stop_reminders": false, "quiet": false},
  "adopted_from": null
}
```

Box `status` is one of `open` (nothing done), `pending` (a background job is
working on it), `done`, `skipped` (the client chose not to), `not_applicable`
(the machine found nothing to do, e.g. no notetaker). The install is complete
when every box is `done`, `skipped` or `not_applicable` AND a re-verification
from disk agrees. `accounts` and `vault` can never be `skipped` (asserted).
`degraded` is set when a usage cap or a provider cap interrupted a step; the
step stays `pending`, never `done`.

`app/setup_state.py` owns this file: `load()`, `save(data)` (atomic),
`new(run_id, platform, plugin_version)`, `box(data, key)`, `set_status(data,
key, status, detail="")`, `open_boxes(data)`, `resolved(data)`, `verify(data)`
(re-checks every box from disk and returns the list that changed),
`one_line(data)` (the shared nudge line, under 120 characters, naming the open
count, the one next action and where to type it), `remove()`. Every lane
reads and writes the checklist through this module only.

## The orchestrator (`app/setup_flow.py`)

Subcommands, each printing exactly one JSON object on stdout:

- `prepare --run-id ID [--platform]`: called by the setup script after the
  venv exists. Creates the checklist (or resumes it), stamps `started`, ticks
  `plugin`, `python`; finds the key file (`companion_key.discover`), installs
  it (`companion_key.install_all`), moves it to `S/keyfile/`, ticks
  `credentials`; installs the nudge job. Never asks anything.
- `next [--answer-stdin]`: the loop the skill calls. Reads an answer from
  stdin when the flag is given (`{"id": "...", "value": ...}`, delivered by a
  quoted heredoc so a notetaker key never reaches a command line). Runs
  machine steps until a human or a join is needed, then prints ONE of:
  - `{"kind": "prompt", "box": "...", "prompt": {"id": "notetaker|confirm|schedule|priorities|deals", "header": "...", "question": "...", "options": [{"label": "...", "description": "..."}], "multi": false}, "line": "<progress line>", "elapsed": "mm:ss"}`
  - `{"kind": "wait", "box": "...", "on": "scan|summaries|research|jobs", "say": "reading your mail...", "poll_seconds": 15, "elapsed": "mm:ss"}` (the skill shows `say` once, sleeps, calls `next` again)
  - `{"kind": "dispatch", "agents": [{"name": "week", "prompt": "/van-gogh:week"}, {"name": "morning-coffee", "prompt": "/van-gogh:morning-coffee"}], "say": "two briefings being written", "elapsed": "mm:ss"}` (the skill launches both with the Agent tool in one message, waits for both, then calls `next` again)
  - `{"kind": "done", "report": {...the report...}, "closing_text": "...", "elapsed": "mm:ss"}`
  - `{"kind": "error", "box": "...", "line": "<one line, at most 120 chars, includes the literal resume command /van-gogh:install-van-gogh>", "elapsed": "mm:ss"}`
  Exactly five prompt ids exist in the happy path: `notetaker`, `confirm`,
  `schedule`, `priorities`, `deals`. A sixth, `account_mismatch` (use this
  account anyway), appears only when the signed-in address differs from the
  key file's hint.
- `status`: the checklist as a tick list (`lines` plus `open`, `total`, `line`).
- `report [--ledger PATH]`: splits machine, human and model time from the
  timing ledger, re-reads finish-line facts from disk, prints `TOTAL mm:ss of
  15:00`, exits 0 only when complete, under target and not degraded.
- `finish`: re-verifies every box, writes `complete.json`, deletes the
  checklist, removes the nudge job, prints the closing text with the computed
  first-email clock time in the client's timezone.
- `reset`: deletes the checklist and stamp, keeps the ledger.
- `verify-stamp [--live]`: the under-5-seconds local check on every command
  (token file present and unexpired, jobs registered, vault resolves); with
  `--live` (at most once a day, stamped) refreshes one token. A broken box
  reopens the checklist and prints the nudge line.
- `adopt`: once per plugin version at the first command after an update:
  builds a checklist from disk for an install that predates this flow,
  finishes machine-only boxes, writes the stamp silently when nothing is open,
  else installs the nudge job. Credential rules: a missing credential is
  written; a differing one with a working sign-in is kept with a warning line;
  a differing one whose refresh fails is replaced.
- `nudge [--maybe] [--now ISO]`: the nudge job entry. Finishes machine-only
  boxes first, then decides whether to send (cadence below), sends by email
  from the client's own account when a sign-in works, else a desktop
  notification, stamps the checklist, removes the job after the third send.
- `nudge-job install|remove|status`: the standalone job.
- `_job NAME ...`: hidden wrapper for detached work (`start_new_session=True`,
  stdio to the logs dir), writes a pid and a result file under `S/setup/`.

Timing ledger rows: `{"ts": iso, "run_id": ..., "event": "start|step_begin|step_end|prompt_shown|answer_received|job_begin|job_end|dispatch|done", "step": "...", "class": "machine|human|model", "detail": "..."}`.
`report` measures each class from paired events, never as a remainder.

Launch line the setup script ends with (Mac):
`claude --permission-mode acceptEdits --allowedTools 'Bash(<S>/venv/bin/python *)' 'Agent' "/van-gogh:install-van-gogh"`.
Proven in the access audit: an opening slash prompt runs a
`disable-model-invocation: true` skill, and a launch-time allow rule reaches
subagents. The Windows form uses `<S>\venv\Scripts\python.exe`.

Keep-awake: `caffeinate -i -w <orchestrator pid>` on macOS, started by the
first `next` and bound to the pid; Windows uses
`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` in the long
running `_job` children. A bell (`\a` to the terminal plus the macOS
notification sound) fires only when a prompt has waited more than 30 s.

## Key file v2 (`tools/keyfile.py` builds it; `app/companion_key.py` reads it)

```json
{
  "kind": "van-gogh-key", "version": 2, "issued": "2026-10-01",
  "repo": "NVentures/project-van-gogh-custom-first-last", "token": "...",
  "credentials": {"kind": "van-gogh-credentials",
                  "google_client_secret": {"installed": {...}},
                  "ms_client_secret": {"client_id": "...", "tenant_id": "common"},
                  "maps_api_key": "..."},
  "accounts": [{"provider": "google", "email_hint": "jane@company.com"},
               {"provider": "microsoft", "email_hint": "jane@corp.com"}],
  "client": {"full_name": "Jane Doe", "first_name": "Jane", "company": "Acme",
             "timezone": "America/New_York", "notetaker": "granola"},
  "support_email": "team@example.com"
}
```

Version 1 files (`kind`, `repo`, `token`, `issued`) keep working. The shared
credentials come from the private `credentials/shared-internal.json` at the
internal repo root (never under `project-van-gogh/`). Discovery looks in
`~/Downloads`, `~/Desktop`, the current folder and `S/keyfile/`, accepting
`van-gogh-key*.json` including browser-renamed variants. `install_all` returns
`{"ok", "mode", "credentials": {...}, "companion": {...}, "accounts": [...],
"client": {...}, "support_email", "moved_to"}`.

## Pipeline speed (`app/fanout.py` and the warm start)

- `fanout.run(items, fn, width, key=None, retry_on=(), attempts=3, backoff=(2, 5, 10))`
  returns `{"filed": [...], "failed": [...], "deferred": [...]}`; the three are
  disjoint and their union is the input; output order is the input order.
- `VAN_GOGH_SERIAL=1` makes every width 1 and every pool a plain loop, so the
  honest control reproduces today's behaviour. `VAN_GOGH_MODEL_SLOTS` (default
  3) bounds concurrent model calls in `claude_cli` across threads of one
  process; a throttle is retried; a usage cap raises `claude_cli.UsageCap`
  (classified by `failure_class`, which must not read another provider's
  "usage limit exceeded" as the cap).
- `week_review.py --save-scan PATH` writes the scan snapshot; `--reuse-scan
  PATH` renders a briefing from it instead of fetching. The Outlook reply
  check runs through `fanout` and records a failed lookup as an error entry,
  never as "no reply".
- `meeting_ingest.py --prefetch --limit N --staged-dir D` writes summaries
  only, 3 at a time; `--staged D --apply` files them serially. The nightly
  path (`--meeting uningested --apply ...`) is unchanged byte for byte.
- `warm_start.py` gains `wait-scan [--timeout S]`, `ingest-start --limit 10`
  (detached prefetch), `ingest-finish` (file what was staged, report filed,
  failed, deferred), `first-briefing --which week|morning-coffee|afternoon-tea`
  (data prepared from the saved scan, no second fetch).

## Nudges

Three surfaces, all gated on `S/setup/checklist.json` existing and silent
otherwise (exit 0, empty stdout, empty stderr):

1. Session-start hook: `hooks/hooks.json` (SessionStart) running
   `hooks/setup_notice.sh` (bash) which prints `{"systemMessage": "<line>"}`
   and runs `setup_flow.py nudge --maybe` detached; under 1 s; silent when
   `VAN_GOGH_UNATTENDED=1` or `VAN_GOGH_SCHEDULED=1`.
2. In-command line: `meta.setup_notice` from `config_loader` (via
   `setup_state.one_line`), rendered by `skills/_shared/front-page.md` as a new
   step 14 (one line, then once offer resume, skip this step, or stop reminding
   me). Also in the digest email footer.
3. The nudge job: daily at 09:00 local, `setup_flow.py nudge`.

Cadence: sends on day 1, 3 and 7 after `stall_started`, at most one send per
24 h however many times the job fires, cadence restarts on a new stall (a
resume clears `stall_started`; a later stall sets it again), after the third
send a "went quiet" ledger line and the job removes itself. Every run appends
`{"when", "sent": 0|1, "reason"}` to `nudges.runs`. Email: subject "Two
minutes to finish setting up Van Gogh", one action, where to type it, who must
act when it is not the client, the help address in the body
(`support_email`), no mail content, no secrets, no list of everything
unfinished. Sent with `send_email.send_email` from the client's own account to
the client's own address; when no sign-in works, one desktop notification per
24 h instead. `stop_reminders` stops the email and the job; the hook line stays.

## Setup scripts

`setup.sh` and `setup.ps1` at the plugin root (run from the marketplace
clone), `check.sh` and `check.ps1` at the public repo root. Two lanes at once:
plugin and companion install; Python env from an existing 3.10+ else a private
`uv` under `S/tools/uv` with a managed Python (bootstrap only, never invoked by
a skill). No sudo, brew, winget, choco, RunAs or admin dialog anywhere
reachable. Refuses inside a Claude session (`CLAUDECODE` set) or cmd.exe.
Writes `S/setup/run-id`, `S/requirements.sha256`, calls `setup_flow.py
prepare`, then launches Claude with the line above. `--prepare-only` stops
before the launch. `--check` prints READY or the missing items.

## Versions and docs

Version 0.81.0 in `pyproject.toml` and `.claude-plugin/plugin.json`, one
CHANGELOG entry (plain words, `New:` / `Improved:` / `Fixed:`), README install
section rewritten with no unbacked time claim, FOUR-CS.md updated for the
transient nudge job. The docs lane does this last.
