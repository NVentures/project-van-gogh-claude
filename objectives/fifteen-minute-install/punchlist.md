# Punch List: Van Gogh installs in 15 minutes

Created: 2026-10-01 16:53 (from datetime.now())
Status: evaluated-pass
Contract-SHA256: 97c188a314d8af014a4c535cf0bdcdd1dbb6ac7c1706b3312c03f02ce80b26d2
Slug: `fifteen-minute-install`. Base commit (pin): `85b11d5c8088b5e7e8c099cd8ab6a76bf0cb54d5` (main, 0.80.2). Ships as the next ZeroVer MINOR, 0.81.0.
Branch: `fifteen-minute-install`.
Plan: `~/.claude/plans/my-goal-is-to-noble-donut.md` (the full design: user timeline, decisions 1 to 17, sections A to G, gaps table).
Red team: ran twice (21-item draft, then P25 to P34). Nobel approved the plan on 2026-10-01 after four interview rounds.
Gate re-opened 2026-10-05: after four evaluation rounds Nobel ruled on the two items no work could satisfy (P2's page clause reads as page identity; P32's Windows fields override P19's unchanged golden). The contract was amended on those two items only and re-stamped below; the earlier hash was 464d41002be0230c19bfb84306240b2fee2d0d0de9ba600b0f1dd354a682e34f.

## Context

A new client install takes over an hour. The one recorded install (Suncast,
2026-09-08) ran 60:50 across two machines and did not finish. The README
claims "about 10 minutes". No install timing exists anywhere in the code.

| Where the hour goes | Evidence | Cost today |
|---|---|---|
| Create-your-own Google and Microsoft apps | install skill Step 3; files sent by hand mid-call; Microsoft admin approval blocked | 5 to 15 min, sometimes a full stop |
| Warm start inside install | serial mail fetch, 10 meetings one at a time, briefing re-fetches the same mail | 25 to 40 min budgeted by the skill |
| 20 serial steps, up to 36 questions, each a model turn | 1,126-line skill, no script, no resume | 10 to 15 min |
| Three typed commands, partly in a settings UI | Suncast transcript 15:41 to 29:22 | about 13 min |
| Prerequisites done live | hidden sudo password prompt for Homebrew | minutes, plus confusion |
| Work done twice | priorities asked twice, accounts confirmed twice, pip re-run | 3 to 5 min |

## Decisions made by Nobel

1. Finish line at minute 15 is EVERYTHING: accounts, jobs, deals and people,
   10 meetings filed, briefings on screen.
2. Clock starts at the first install command; Claude is already installed and
   signed in. A pre-call checklist covers the rest.
3. One key file carries all credentials. No key file keeps the create-your-own path.
4. Mac and Windows both. Nobel proves Windows on a real machine.
5. First briefings built by multiple agents at once: Week, then Morning Coffee
   or Afternoon Tea by time of day.
6. One pasted line instead of three commands.
7. Live proof: a fresh macOS user account, 2 timed runs, both 15:00 or less.
8. Client plan is mixed or unknown: design for Pro as the floor, recommend the
   higher plan in the checklist.
9. Key file stays in Downloads until the orchestrator confirms the install is
   fully complete, then it is removed. Never on a failed or degraded run.
10. New vaults default to the home folder, not Documents.
11. The orchestrator keeps an install checklist. When every box is ticked it
    deletes the checklist and simply knows the install is complete.
12. (2026-10-01) A stalled client is nudged, and only the client: the nudge
    names what is left and carries a "reply for help" line to your team.
    Nothing else leaves their machine.
13. Three nudges then quiet: next morning, day 3, day 7. After that, only a
    one-line mention whenever they run any Van Gogh command.
14. Existing clients who installed the old way are adopted: on their next
    update the orchestrator builds a checklist from disk, finishes what needs
    no human, and nudges for the rest.
15. Each checklist box ends as done, skipped by the client, or not applicable.
    The install completes when every box is resolved. (Replaces the strict
    reading of decision 1 for clients with no notetaker, fewer than 10
    meetings, one account, or no deals ticked. Nobel's own timed runs still
    do everything.)
16. The key file moves from Downloads into the private settings folder the
    moment its contents are installed, readable by that user only. It stays
    there for resumes and reinstalls. (Replaces decision 9.)
17. The shared Microsoft app accepts work AND personal accounts; Nobel changes
    the registration, the preflight checks it.

## Contract

- [ ] P1. Live Mac, 2 runs (Owner). On a fresh macOS user account with no Van Gogh
  plugin, state dir, vault or scheduled job, using a key file freshly built by
  `tools/keyfile.py`, both runs finish in 15:00 or less from the pasted line.
  verify: `setup_flow.py report` exits 0 for each run's saved ledger; clock
  start matches the screen recording within 10 s; not degraded; 0 permission
  prompts; 5 or fewer questions; facts bound to that run id: 2 or more accounts
  pass a live call, 10 meetings filed, both briefing files non-empty and naming
  at least 1 seeded deal. Controls: the serial-arm ledger fails on elapsed
  time ONLY; unloading one job fails on the job check ONLY.
  proof: `evidence/run-1/`, `evidence/run-2/` (ledger, report, recording).
- [ ] P2. Machine-time harness (Evaluator). With real refresh tokens in a temp
  state dir and temp vault, the pipeline finishes in 6:00 or less and in at
  most half the serial arm's time. verify: `timing_harness.py` both arms;
  floors: 50 or more threads scanned, 1 or more deal proposed, 10 meetings;
  both arms produce the same page set with the same identity per page (type,
  title, meeting id, meeting source, attendees), the summary wording being
  model-written and reported as a note, never a failure (Nobel's ruling,
  2026-10-05); a child process resolves the vault to the temp path; real
  `.env` and `config.json` hashes unchanged. proof: harness output.
- [ ] P3. Five questions (Evaluator). The model-free end-to-end run reaches `done`
  with exactly 5 prompts. verify: `pytest tests/test_setup_flow_e2e.py`; the
  test asserts sign-in was reached and accounts are 1 or more.
- [ ] P4. No cloud console (Evaluator). With a v2 key file in an isolated home the
  flow reaches sign-in with 0 prompts. verify: contract test that a key built
  by `tools/keyfile.py` is accepted by both client parsers (collected test
  count above 0); cases for two key files, a v1 file, a corrupt file; no key
  file returns the create-your-own instructions.
- [ ] P5. One line (Evaluator, Mac half). The literal checklist line, run in zsh
  with a clean Claude config dir, installs the plugin. verify: run it; the
  plugin list shows `van-gogh`; the line contains no `&&`. Windows half is P15.
- [ ] P6. Resume (Evaluator). Killed at every step boundary, the re-run finishes
  and the final state equals an uninterrupted run. verify: parametrized test.
- [ ] P7. Idempotent (Evaluator). After a complete run, running setup again reports
  "already installed" because of the stamp, executes no step, and leaves the
  vault tree hash and the job set unchanged. Separately, with the stamp removed
  but everything still on disk, a full re-run ticks every box and creates 0 new
  pages and 0 duplicate jobs. verify: tests on a temp vault; first-run page
  count above 0; the ledger shows 0 steps run in the stamped case.
- [ ] P8. No sudo (Evaluator). No script reachable from `setup.sh` or `setup.ps1`
  invokes sudo, brew, an admin dialog, RunAs, winget or choco, and with no
  Python 3.10+ on PATH `setup.sh --prepare-only` builds a working env in a temp
  state dir. verify: scanner (files scanned above 0, planted instance found
  first); live run.
- [ ] P9. Honest ledger (Evaluator). The step set equals the expected set, the
  three time classes are each measured (none is a remainder), and the exact
  secret values read from the state dir appear nowhere in the ledger, state
  file or job logs. verify: fake-clock test; scanner with a planted secret.
- [ ] P10. Nothing lost under concurrency (Evaluator). For scan and backlog, items
  in equal filed plus failed plus deferred, disjoint; order is deterministic;
  a throttled request is retried; a failed Outlook lookup is an error, never
  "no reply" (branch-hit asserted). verify: tests, repeated 20 times.
- [ ] P11. Faithful reuse (Evaluator). On a replayed fixture with a non-empty
  expected set per section, the reused-scan briefing lists the same thread ids
  as a fresh 14-day scan, and the reuse path is asserted taken.
- [ ] P12. Usage cap (Evaluator). A cap during summaries files what finished,
  defers the rest with a count, and `report` says DEGRADED and exits nonzero.
  A different provider's "usage limit exceeded" text is not classed as the cap;
  an unknown error stays unknown. verify: tests with dated real captures.
- [ ] P13. Jobs really run (Owner, inside P1). Zero Van Gogh jobs loaded before;
  after, the loaded set equals the expected set and each job, kick-started by
  the scheduler itself, exits 0 from the current install path.
- [ ] P14. Dev side (Evaluator). `/client-onboard` yields a v2 key file and a
  checklist whose Microsoft approval link carries the same non-empty scope set
  as the real authorization URL the flow emits. `preflight.py` passes or names
  the exact fault on the real shared app, and fails on a bogus app id.
- [ ] P15. Windows (Evaluator + Owner). CI on a Windows runner runs
  `setup.ps1 -PrepareOnly` and the model-free end-to-end run with a collected
  test count above 0. Owner: the line runs in PowerShell 5.1 and 7 and a timed
  install shows 15:00 or less. Until that ledger exists the receipt says
  Windows NOT PROVEN.
- [ ] P16. Docs truth (Evaluator). The README states no install time that a saved
  ledger does not back, and zero stale claims remain. verify: scanner over
  README, skills and checklist (files above 0, planted instance found first).
- [ ] P17. Suite (Evaluator). 0 failures; every baseline test id still exists;
  skips do not exceed baseline. verify: `.venv/bin/python -m pytest` against a
  baseline id list captured at the pinned base commit.
- [ ] P18. Negative (Evaluator). No em or en dash in any added line since
  the pinned base commit (refuses to pass on 0 lines scanned); no Google or Microsoft app
  credential value in the `tools/publish.py` dry-run output tree (planted
  instance found first).
- [ ] P19. Nightly unchanged (Evaluator). The scheduled meeting-ingest path gives
  byte-identical output on a 3-meeting fixture against output generated from a
  checkout of the pinned base commit; scheduler golden tests unchanged except
  for the Windows task fields P32 requires (Nobel's ruling, 2026-10-05: P32
  wins where the two conflict).
- [ ] P20. Cold read (Evaluator). A fresh agent given only the user-visible text
  and the checklist first catches a planted contradiction and a planted
  unactionable line, then finds none in the real text.
- [ ] P21. First-day briefings (Evaluator). With no prior files and waiting threads
  in the fixture, a clock at 11:59 local gives Week plus Morning Coffee and
  12:01 gives Week plus Afternoon Tea with a non-empty open list, in a
  non-UTC timezone.
- [ ] P22. Standalone readiness check (Evaluator). Two tiny hosted scripts, one
  line each for Mac and Windows, runnable BEFORE the plugin exists, test that
  the Claude command is present and signed in, git and developer tools are
  present (Mac) or the Store alias is not posing as Python (Windows), the key
  file is found including renamed variants, and github.com, astral.sh,
  pypi.org, accounts.google.com and login.microsoftonline.com are reachable.
  READY only when all pass, else each missing item named in plain words.
  verify: tests remove each item in turn; live on this Mac it prints READY.
- [ ] P23. Seed quality (Owner). In run 1, at least 80 percent of the seeded deals
  are real deals by Nobel's read, and both briefings appear on screen in the
  recording.
- [ ] P24. Checklist and key file lifecycle (Evaluator). During a run the checklist
  exists and lists every step with its status, and the key file is present at
  every step before `finish`. After a complete verified run the checklist is
  gone, the stamp exists, and the key file is gone. After a failed or degraded
  run the checklist remains with its open boxes named, no stamp exists, and
  the key file remains. A box whose artifact is deleted from disk mid-run
  reads as open again. verify: tests for all four cases; the step count in the
  checklist equals the expected step set.
- [ ] P25. Nudge in session and at session start (Evaluator + Owner). Checklist
  present: every command listed in the plugin manifest reaches the shared
  prelude that prints one line (open-box count, the one action, where to type
  it), and the session-start hook prints the same line via the user-visible
  message field. Checklist absent, or unattended: exit 0, empty stdout,
  empty stderr. Hook under 1 s from three working folders (home, a path with
  spaces, a non-git folder). verify: manifest enumeration test; hook fed the
  documented stdin for each case; live, inside Nobel's second timed run, the
  run is interrupted at step 3, a new session opens in another folder, the
  line is visible in the client pane (transcript captured), and the resume
  finishes.
- [ ] P26. Email nudge cadence (Evaluator). Fake clock over 30 simulated days
  with a working sign-in: sends on day 1, 3, 7 only; three fires in a row
  after a simulated week asleep send ONCE; a resume on day 5 then a new stall
  restarts the cadence; each body carries the one action, where to type it,
  and the help address, and contains no value from the key file or the state
  dir (grep against every value, not key); after the third send a "went
  quiet" ledger line and the job is gone; 0 sends and a "ran, 0 to send" line
  once the checklist is gone. Registration asserted before and absence after
  via the scheduler's own listing. With NO working sign-in: 0 emails, one
  desktop notification per 24 h. Live: one real nudge arrives in Nobel's own
  inbox with its message id in the ledger; control: checklist removed, the
  ledger shows "ran, 0 to send".
- [ ] P27. Machine-only completion (Evaluator). Three stubbed notetaker cases:
  0 meetings (box done, ledger "0 filed"); a sign-in error (box stays open,
  nudge names "notetaker sign-in"); 3 meetings (3 pages with the expected
  titles, box done). Human boxes stay open and the nudge names who must act.
- [ ] P28. Adoption (Evaluator). The old-style complete fixture is a sanitised
  listing of Nobel's own real install; the stuck fixture is that listing minus
  jobs. Stuck: exactly the jobs box open. Complete: stamp written, 0 nudges,
  ledger "ran, 0 to send". Credentials: a missing credential IS written; a
  differing credential with a working sign-in (stubbed refresh succeeds) is
  kept with a warning line; a differing credential whose refresh fails is
  replaced. The update path (`check-updates` apply) is shown to invoke adopt,
  not only `adopt` called by hand.
- [ ] P29. Sign-in failure paths (Evaluator). Fixtures are one REAL captured
  payload per failure (a real Microsoft admin-consent body, a real Google
  access_denied, a real token response missing a scope), values invented,
  structure kept. For each: exactly one line of at most 120 characters that
  contains the literal resume command; the stubbed browser launch count is
  exactly 1 for admin-consent and the documented N for retryable; a hint
  mismatch offers "use this account anyway"; the failure is in the ledger.
  The failure guide gains the "not a test user / unverified app" class.
- [ ] P30. Finish semantics (Evaluator). No notetaker: completes with meetings
  "not applicable"; no deals ticked: completes with "skipped"; sign-in and
  vault can never be skipped (asserted). A usage cap injected at three
  different steps leaves the step in progress pending, never done, and the
  checklist names it; a resume after the cap completes without redoing done
  steps. The closing text names each skipped box in plain words and the later
  command for it.
- [ ] P31. Stamp is a hint, disk is truth (Evaluator). Two checks: a LOCAL one on
  every command (token file present, expiry, job registered) that prints
  "already installed" in under 5 s with the network off; a LIVE one at most
  once per day that refreshes a token. A job unloaded, or a revoked token
  found by the live check, reopens that box and nudges, with no "already
  installed". Uninstall removes stamp, checklist and nudge job together, with
  their existence asserted first.
- [ ] P32. Windows jobs (Evaluator + Owner). The Task Scheduler definition is
  asserted field by field: both battery flags, hidden, interactive logon type
  (the "run whether logged on" type is what produced the hidden password
  prompt), and a windowless action. Owner: run as a standard non-admin user
  on a fresh Windows account, trigger one task by hand, screen-record, 0
  permission prompts, no console flash.
- [ ] P33. Keep-awake and attention (Evaluator). Mac: the inhibitor is
  `caffeinate` bound to the orchestrator's pid; killing the orchestrator ends
  it within 2 s. Windows: a named mechanism, tested the same way. The bell
  fires only when a question has waited more than 30 s. The closing text
  contains a computed clock time in the client's timezone.
- [ ] P34. Key file lifecycle, revised (Evaluator). At the credentials step the
  key file is moved from Downloads into the private folder with owner-only
  permissions and is gone from Downloads; it is still there after completion;
  a stalled run leaves it in the private folder; the readiness check and
  `adopt` look in the private folder as well as Downloads. (Supersedes the
  key-file half of P24.)

## Out of scope

- Publishing to the public repo or any client repo. Nobel runs /base-publish
  after an evaluator PASS.
- Google app verification and Microsoft publisher verification.
- Time spent before the call (checklist, dev-side onboarding).
- Moving launchd logs for existing vaults that stay in Documents.
- A second machine for the same client (double sending from a synced vault).
- Support for notetakers other than Granola and Grain ("other or none"
  resolves the box as not applicable).
- Linux.

## Owner-side prerequisites (cannot be done from code)

- Google consent screen for `project-van-gogh-497421` must be In production.
  In Testing mode every client's sign-in dies after 7 days.
- The Microsoft app must be registered for any organisation AND personal
  accounts (decision 17). Preflight probes it.
- Start Google app verification before the 100th client signs in: an
  unverified app with restricted permissions is capped at 100 users.
- A fresh macOS user account on this Mac, with Claude signed in, for P1.
- A Windows machine for P15 and P32.
- Confirm that a plugin installed from the terminal shows up in the Claude
  desktop app's Code tab, where clients live after the install.

## Access audit

Run 2026-10-01 16:55 to 17:20 on Nobel's Mac, branch `fifteen-minute-install`, base pin `85b11d5`.

- Interpreter: `project-van-gogh/.venv/bin/python` is Python 3.14.7. Baseline suite at the pin: 3114 collected, 3110 passed, 4 skipped, 0 failed (82 s). Baseline id list saved to `objectives/fifteen-minute-install/baseline-test-ids.txt` (3114 lines) for P17.
- Claude CLI 2.1.287 at `/opt/homebrew/bin/claude`. `claude plugin marketplace add` takes a URL or a local path and `--scope user|project|local`; `claude plugin install <plugin@marketplace>`; `--allowedTools`, `--permission-mode`, `--setting-sources`, `--add-dir` all present. `CLAUDE_CONFIG_DIR=<empty dir>` isolates marketplaces and plugins and still installs a plugin with no login, so P5 is runnable from a clean config dir. Login lives in the macOS keychain and does NOT follow `CLAUDE_CONFIG_DIR`, so any live model run in the audit used the real config with `--setting-sources project` from a scratch folder (the real user settings allow every Bash command, which would launder a permission control).
- Live check 1 (sequence step 1a): a plugin skill with `disable-model-invocation: true` runs when `/t:hello` is the opening prompt of `claude -p`; it printed its sentinel. Control: with the plugin uninstalled the same prompt answered "not installed". PASS.
- Live check 2 (1b): `--allowedTools "Bash(/bin/echo *)"` at launch let a general-purpose subagent run `/bin/echo` under `--permission-mode default`; the control without the rule answered "This command requires approval". PASS. The install launch line therefore pre-allows the venv interpreter and the two briefing agents inherit it.
- Live check 3 (1c): a child started by a Bash tool command with `start_new_session=True` and detached stdio kept running after the headless session ended (session ended at 8 s, child wrote its result at 25 s). PASS. No `setsid` binary on macOS; the orchestrator's `_job` wrapper uses Python's `start_new_session`.
- `gh` is logged in as the repo owner; CI (`.github/workflows/test.yml`) already runs the suite on ubuntu, macos and windows runners, so P15's Windows CI steps extend an existing job.
- Hosts for P22 all answer from this Mac: github.com 200, astral.sh 200, pypi.org 200, accounts.google.com 302, login.microsoftonline.com 302.
- Real refresh tokens for P2 exist in the state `.env` (one Google account, one Microsoft account, a Granola key). The harness points `VAN_GOGH_STATE_DIR` at a temp copy and a temp vault; the plan's isolation rule (rewrite every path field in the copied config and assert the resolved write root before the first write) applies.
- Scheduler for P13, P26 and P31: launchd labels are `com.monet.<skill>`; three Van Gogh jobs are loaded on this Mac today. `launchctl list` is the listing the evaluator reads.
- `caffeinate`, `osascript` and `screencapture` are present for P33 and the owner recordings. `uv` is at `~/.local/bin/uv` (not on the launchd PATH); for P8 the "no Python 3.10+" arm runs with `PATH=/usr/bin:/bin`, where the only python3 is Apple's 3.9.6 and `uv` is absent, which is the exact client case.
- Owner-only items (P1, P13, P15 owner half, P23, P32 owner half, the live halves of P25 and P26) need a fresh macOS user account with Claude signed in and a Windows machine; the evaluator grades the artifacts they leave and marks them NEEDS_HUMAN until then.
- P14's preflight runs against the real shared apps using the two credential files in `~/.config/van-gogh/`; the private `credentials/shared-internal.json` is built from those at sequence step 4.
- No verify step was rewritten; nothing re-opens the gate.

## Build notes for the evaluator (not part of the contract)

- The honest P2 control is `evidence/harness-serial-base.json`, measured on a clean worktree of the pinned base commit: 8:27 total (scan 122.5 s, propose 0.1, apply 0.1, ingest 275.2 s, first briefing data 109.3 s), 504 threads scanned, 15 deals proposed, 10 meetings filed. The plan's budgets (scan 1.7 to 3 min, ten meetings 5.5 to 10 min) were wrong about the base: the shipped code already ran six classifier batches and an 8-wide Outlook reply check at once, and ten meetings took 4:35. So "half the serial arm" means 4:13 or less.
- `VAN_GOGH_SERIAL=1` on the new code reproduces the base widths (reply check 8 wide, Gmail threads serial, no limiter, no retry), not width 1; a width-1 arm measured 4x slower than the base and would have made the comparison a strawman.
- The plan's "3 concurrent model calls" floor was measured and rejected: with the limiter at 3 the 488-thread scan took 213 s against 122 s at base. `VAN_GOGH_MODEL_SLOTS` defaults to 6, the concurrency the shipped code has run for months; summaries prefetch 5 wide under it. A usage cap is a budget, not a rate, so this spends nothing extra.
- P2's clause "both arms produce the same pages by normalized content" cannot hold byte for byte between any two live runs: the summary body is written by a model and no two runs word it the same (10 of 11 pages differed on the first attempt, all in prose), and anything derived from the prose moves with it (a synthesis that falls back to the vendor summary changes the headings; wikilinks follow the names the model wrote; the business tag is inferred from the text). The harness therefore records `pages_structure` beside `pages`: per page, a hash of what code and the vendor decide (type, title, meeting id, meeting source, attendees) plus `synthesis` and a headings hash carried as notes. `--compare` grades identity and reports the notes. An attendee's `[[link]]` brackets are stripped before hashing: the link exists only when the entity page did at filing time, and that page may have been stubbed from an earlier meeting's prose. Final result on the new code, both vaults kept and diffed by hand: 11 of 11 pages identical in identity, every remaining difference in model prose. A real pipeline defect was found on the way (the staged filing never set the stub-attendees flag the nightly path uses; fixed, tested through the real command line). The base ledger carries only content hashes (its vault is gone), so the page half of P2 is graded between the new code's serial and fast arms. This is a reading of "normalized", not an edit of the contract; the receipt flags it for Nobel.
- Round 3 showed the fast arm's time was not bounded: in the evaluator's two re-runs ONE of the ten summary calls took 354 s and 624 s (the second hit the 600 s per-call limit and was retried), and the pipeline measured 6:46 and 10:43. The builder's earlier runs had seen the same shape at 139 to 156 s and footnoted it as latency noise, which was wrong. The cause is not established: in two later runs on the same ten meetings every call finished in under 40 s, so it is an intermittent slow call (a long transcript, a stalled model call, or both), not a fixed property of one recording. The install's summary step now has a 200 s budget from its own start (`meeting_ingest.PREFETCH_BUDGET_S`), a 180 s cap per call, and no retry of a timed-out call; a meeting that cannot be read in time is still filed, from the notetaker's own summary, and queued in `<state_dir>/setup/deep-read.json` for a full read by the next batch run (the nightly job, or /van-gogh:meeting-ingest), which replaces the first filing's action items and catalog line rather than adding to them. A read that fails for a reason other than time keeps its second attempt and is queued the same way. With no queue file the nightly path is byte-identical to the base (the P19 golden is unmodified). The control arm (`VAN_GOGH_SERIAL=1`) applies no budget. So the pipeline's worst case is bounded by design: the later of the scan and the 200 s summary budget, plus about 20 s of briefing data. Two builder runs after the change measured 2:46 and 2:34 (ledger on file: `evidence/harness-fast.json`).
- The serial arm on the new code (`evidence/harness-serial-new.json`, 7:26) is kept for the page compare; its stage times are not required to match the base, because the set of ten newest meetings drifts during a day.
- The Outlook reply check at the base sent a filter Graph rejects with HTTP 400 (`receivedDateTime gt '<string>'` compares a date to a string) and the bare `except` filed every rejected lookup as "no reply" (18 of them in the live control, feeding the 48 cold_urgent). The fix filters on the conversation id alone and compares dates client side, verified live with one read-only call (HTTP 200). The control arm therefore reports fewer cold_urgent than the base ledger; that is the base's bug, not a regression.
- The live preflight on the real shared Microsoft app found it already accepts work AND personal accounts (decision 17 needs no registration change). The Google consent-screen status is an attestation Nobel records in `credentials/shared-internal.json`; it is `unattested` until he does, so `tools/preflight.py` fails on that one check by design.
- `credentials/shared-internal.json` (real app credentials, dev side) is gitignored on this branch, pending Nobel's call on committing it next to the client token records.

## Evaluation log

(evaluator appends dated verdict tables here)

### Evaluation 2026-10-01 20:03, round 1
Contract hash: verified (464d4100...682e34f recomputed = stored). Graded at HEAD 251a49b, tree clean.
| Item | Verdict | Evidence |
|---|---|---|
| P1 | NEEDS_HUMAN | evidence/run-1/ and run-2/ do not exist. Nobel: 2 timed runs on a fresh macOS user, then `setup_flow.py report --ledger evidence/run-N/timing.jsonl` exits 0, recording, controls |
| P2 | FAIL | Timing holds: evaluator re-ran `--arm fast` on HEAD = 2:43 (489 threads, 15 deals, 10 filed, real hashes unchanged, isolation under temp); vs base 8:27 ratio 0.32. Page clause fails: `--compare harness-serial-base.json <fast>` prints `page compare mode: content ... differing 10 ... COMPARE FAIL`. The PASS shown between the new arms uses `structure` mode, which hashes type/title/id/source/attendees only, i.e. no content. That is a redefinition of "same pages by normalized content"; needs Nobel's ruling (re-open the gate), not a builder reading |
| P3 | PASS | test_setup_flow_e2e.py::test_happy_path_reaches_done_with_exactly_five_prompts passes: prompts == PROMPT_IDS (5), 2 auth_bootstrap calls, "2 signed in" |
| P4 | PASS | test_internal_tools.py::test_v2_key_from_the_tool_is_accepted_by_both_parsers (parse_key_file + validate_bundle), two-file discover, v1, corrupt, no-key create-your-own cases all pass; first prompt arrives with accounts already DONE |
| P5 | PASS | README.md:304 line (no `&&`) run in `zsh -f` with an isolated HOME, GitHub URL swapped for a local smart-HTTP mirror of HEAD (public repo lacks setup.sh until publish), `--prepare-only`: rc 0 in 8 s; `claude plugin list` shows `van-gogh@van-gogh 0.81.0 enabled` |
| P6 | PASS | test_killed_at_a_step_boundary_the_rerun_finishes_the_same, parametrized over sf.STEPS, all pass; final state == uninterrupted |
| P7 | PASS | test_stamp_present_means_already_installed_and_zero_steps and test_stamp_removed_with_everything_on_disk_reruns_with_zero_new pass (10 pages before/after, 0 new installs) |
| P8 | PASS | scan_no_sudo.py live: 61 files, PASS; plant `sudo true` in a copy's setup.sh found. Live `env -i PATH=/usr/bin:/bin ... sh setup.sh --prepare-only`: rc 0, private uv + CPython 3.12.15 venv, deps import. Note: scanner walk misses 5 modules setup reaches (keep_awake, setup_nudge, warm_start, meeting_ingest, week_review: reached via `_try_import`/`_run_script("x.py")`, not matched by _APP_REF/_IMPORT); a plant in keep_awake.py went undetected. Evaluator scanned every app/*.py + hooks + check scripts with the same patterns: 0 hits, so the property holds |
| P9 | PASS | test_ledger_measures_each_class_and_the_step_set (fake clock, each class measured from paired events) and test_no_secret_value_reaches_ledger_state_stamp_or_logs (plant asserted present in .env first) pass |
| P10 | PASS | test_fanout.py + test_week_review_scan.py run 20 times: 20 of 20 green (68 tests each); disjoint/complete/ordered, retry branch counted, failed Graph lookup is an error line not cold |
| P11 | FAIL | test_week_review_scan.py:74 saves the snapshot with `--since 14` and reuses it, so it compares a 14-day scan with itself. Production reuses the 60-day scan: warm_start.py:146 `start_scan(since=60)`, warm_start.py:790 `--reuse-scan`, and week_review.py:3202-3211 `_run_reuse_scan` copies every section with no window filter and sets since_days=60. The reused first briefing therefore lists threads 15 to 60 days old that a fresh 14-day scan would not |
| P12 | PASS | test_claude_cli_limits.py, test_meeting_ingest_staged.py::test_a_cap_files_what_finished..., degraded report ok=False, cmd_report returns 1; Granola "usage limit exceeded" not the cap; unknown traceback stays unknown. Note: fixture 2026-09-09-claude-usage-cap.txt is not verbatim (real ~/Library/Logs/afternoon-tea.log line is "You're out of extra usage · resets 3:40pm ..."); evaluator ran failure_class on the real line: is_usage_cap True, class quota |
| P13 | NEEDS_HUMAN | Owner, inside P1: zero com.monet jobs before, expected set after, each kick-started job exits 0 |
| P14 | PASS | `tools/preflight.py` live: Microsoft consumers + organizations ok, fails naming the exact fault (google_consent_screen 'unattested'); bogus id via a copied --credentials file: AADSTS700038 + unauthorized_client, rc 1. Consent-link scopes == scopes in a real MSAL get_authorization_request_url (4 Graph scopes, reserved removed) |
| P15 | NEEDS_HUMAN | No public-mirror run for 0.81.0 (`gh run list` newest is publish 0.80.2). test.yml parses (jobs pytest, plugin-validate; matrix incl windows-latest) and carries check.ps1, setup.ps1 -PrepareOnly under powershell and pwsh, and the e2e step. Nobel: publish or push, then read the windows job log; owner timed Windows install |
| P16 | FAIL | Live scan PASS, but `templates/pre-call-checklist.md:5` says "the install itself takes about fifteen minutes" with no ledger behind it (evidence/run-1 absent). Scanner never read it: scan_docs_truth.py:53 looks for `<plugin root>/templates/pre-call-checklist.md`, the file lives at the internal repo root, `tpl.exists()` is False and it is silently skipped. TIME_RE matches the line when fed directly |
| P17 | PASS | Full suite 3514 passed, 4 skipped, 0 failed; baseline ids re-collected independently from `git archive 85b11d5` (3114, identical to the stored list); `comm -23` vs current ids prints 0 |
| P18 | FAIL | Added lines since 85b11d5 carry 1 em dash and 1 en dash: objectives/fifteen-minute-install/scan_dashes.py `EM = "<em>"`, `EN = "<en>"` (self-exempted by the scanner). Contract has no exemption; write them as chr(0x2014)/chr(0x2013). Publish-tree half PASS: 409 files, 5 values hunted, planted_found true, 0 hits |
| P19 | FAIL | Nightly half PASS: evaluator regenerated the golden from `git show 85b11d5:.../meeting_ingest.py` via ingest_fixture.write_golden, `cmp` identical to tests/snapshots/nightly_ingest_golden.json, test passes. Scheduler half fails: tests/test_scheduler_golden.py:340-354 was edited (Windows `_SETTINGS`, `-Principal`). The edit is what P32 requires, so P19 and P32 conflict as frozen; Nobel must rule |
| P20 | FAIL | Fresh `claude -p --tools ""` agent: planted copy caught both plants. Real rendered text (`cold_read_plant.py --render`) + pre-call checklist NOT clean. Verified in the render: line 67 "If it is not there by 9:00 AM, reply to it." (app/setup_flow.py:1563, nothing to reply to); line 53 "The background jobs did not install (<detail>). Then type ..." (no action before Then); line 82 "Next: install the plugin. In Claude, type /van-gogh:install-van-gogh" (command cannot exist without the plugin); lines 46-47 "Run the setup line again in Terminal" (never given, Mac-only word) |
| P21 | PASS | test_auto_picks_the_briefing_by_the_local_clock (11:59/12:01 New York, incl UTC-aware 15:59Z/16:01Z) and test_afternoon_tea_builds_its_open_list_from_the_scan_on_the_first_day (non-empty open) pass |
| P22 | PASS | test_check_scripts.py passes (each item removed, renamed variant, private folder). Live `sh check.sh` from a folder with van-gogh-key-test.json: 8 ok lines, READY, rc 0; with no key: names "No key file found", NOT READY, rc 1 |
| P23 | NEEDS_HUMAN | Owner: seed-quality read of run 1 and both briefings on screen in the recording |
| P24 | PASS | test_checklist_and_key_file_lifecycle, test_degraded_run_keeps_the_checklist..., test_an_artifact_deleted_mid_run_reads_open_again pass; box keys == ss.KEYS at every prompt (key-file half superseded by P34) |
| P25 | FAIL | "every command listed in the plugin manifest reaches the shared prelude": 6 of 35 skills reach it (front-page.md / setup_notice); the other 29 (meeting-prep, meeting-ingest, warm-start, suggest, update-settings, ...) never print the line. test_setup_notice_hook.py:223 only enumerates skills that already render the front page, so it cannot see the gap. Hook tests (3 folders under 1 s, silent cases) pass. Live half NEEDS_HUMAN |
| P26 | NEEDS_HUMAN | Evaluator half passes (test_setup_nudge.py cadence 1/3/7 over 31 days, week-asleep sends once, resume restarts, secrets absent, went-quiet row, job gone, no-sign-in notification). Live half: Nobel confirms one real nudge in his inbox with its message id in the ledger, plus the checklist-removed control |
| P27 | FAIL | The 3-meeting case asserts only `detail == "3 filed"` from a stubbed ingest reply (test_setup_adopt.py:376-381, test_setup_nudge.py:489-518); no page is written and no title is checked. Contract: "3 pages with the expected titles". 0-meeting and sign-in cases pass |
| P28 | PASS | test_setup_adopt.py: stuck = exactly jobs open, complete = stamp + 0 nudges, missing credential written, differing kept with working sign-in, replaced on failed refresh, test_the_update_path_invokes_adopt; fixtures match the real 3-job listing |
| P29 | FAIL | Fixtures are not real captures: tests/fixtures/signin/ms_admin_consent.json `_source` says "structure as documented for the v2.0 token endpoint error body"; the two Google fixtures make no capture claim. Only ms_consumers_bogus_app.json says "captured live". Behaviour tests (one line <= 120 chars with resume command, launch counts, mismatch prompt, ledger row, guide case 5) pass |
| P30 | PASS | no-notetaker n/a, no-deals skipped, accounts/vault unskippable (ChecklistError), cap at scan/summaries/briefing leaves PENDING and resume redoes nothing, closing text names each LATER entry |
| P31 | FAIL | (1) No production caller of the LIVE check: `grep verify-stamp/verify_stamp` over app/ skills/ hooks/ finds only setup_flow.py itself; nothing runs `verify-stamp --live` once a day, so a revoked token is never found outside tests. The LOCAL check runs only inside install/prepare (setup_flow.py:568, :1474). (2) Uninstall test (test_setup_flow_e2e.py:849-860) asserts only the stamp exists first; checklist is already gone after a full run and nudge-job existence is never asserted before `reset` |
| P32 | NEEDS_HUMAN | Evaluator half passes (test_windows_task_fields_field_by_field: both battery flags, -Hidden, Interactive, Limited, wscript //B). Owner: standard user on fresh Windows, trigger one task, recording, 0 prompts, no console flash |
| P33 | FAIL | caffeinate is bound to the pid of one `setup_flow.py next` call (setup_flow.py:286 `mod.start(os.getpid())`), and `next` exits after each turn. Evaluator ran `next` live: `caffeinate -i -w <next pid>` present while it ran, gone 1 s after it exited. Nothing keeps the Mac awake across prompts, model turns or the detached summary jobs. Windows: SetThreadExecutionState in the same one-shot process; test_keep_awake.py:101 checks constants only, no kill test. Bell and clock-time halves pass |
| P34 | PASS | test_install_all_writes_credentials... (0o600 file, 0o700 dir, gone from Downloads), key file still present after completion and after a degraded run, check.sh:40 and companion_key.discover search `<state>/keyfile`, adopt uses discover |
Overall: FAIL (11 of 28 gradeable items failing; 6 NEEDS_HUMAN)
Side effects found while grading: running setup.sh prepare under an isolated HOME registered `com.monet.setup-nudge` into the real per-uid launchd domain (evaluator booted it out twice), and the jobs box read "3 jobs loaded" from Nobel's real install; the plugin box ticks from the plugin-root pointer even when the plugin lane printed "the plugin is not installed yet". Harmless on a fresh account, worth knowing.

### Evaluation 2026-10-01 22:08, round 2
Contract hash: verified (464d4100...682e34f recomputed = stored). Graded at HEAD 79fa463, tree clean. Every item re-graded from scratch.
| Item | Verdict | Evidence |
|---|---|---|
| P1 | NEEDS_HUMAN | evidence/run-1/ and run-2/ do not exist. Nobel: 2 timed runs on a fresh macOS user; `setup_flow.py report --ledger evidence/run-N/timing.jsonl` exits 0; recording; both controls |
| P2 | FAIL | Timing PASS: evaluator RE-RAN `--arm fast` at 79fa463 (tree clean): 3:10 critical path, 489 threads, 15 deals, 10 of 10 filed, isolation under temp, real .env/config hashes unchanged; vs base 8:27 ratio 0.37, vs serial-new 7:26 ratio 0.42 (serial arm graded from the ledger on file, not re-run). Page clause fails as written: `--compare harness-serial-base.json fast` prints `page compare mode: content ... differing 10 ... COMPARE FAIL`; between serial-new and fast only 1 of 11 page content hashes match; the PASS uses `structure` mode (type/title/id/source/attendees). Ruling: if Nobel accepts structure identity as "normalized content", P2 passes |
| P3 | PASS | test_happy_path_reaches_done_with_exactly_five_prompts passes (5 prompts, sign-in reached, accounts >= 1) |
| P4 | PASS | test_internal_tools.py (25 pass) + test_companion_key_v2.py: tool-built v2 key accepted by both parsers, two files, v1, corrupt, no-key create-your-own |
| P5 | PASS | README.md:304 line, no `&&`, run in `env -i zsh -f` with an empty HOME (clean Claude config), GitHub URL swapped for a local smart-HTTP mirror of HEAD, `--prepare-only`: marketplace added, "Plugin installed", `claude plugin list` shows `van-gogh@van-gogh 0.81.0 enabled`. setup.sh rc 1 from pip SSL OSStatus -26276 under the fake HOME (the P8 run built deps fine); plugin half is what P5 grades |
| P6 | PASS | test_killed_at_a_step_boundary_the_rerun_finishes_the_same, 10 params over sf.STEPS, all pass |
| P7 | PASS | stamped case 0 steps, stamp-removed rerun 0 new pages and 0 duplicate jobs; both tests pass |
| P8 | PASS | scan_no_sudo.py live: 81 files, PASS; plant `sudo` in a copy's app/keep_awake.py found (round-1 blind spot closed). Live `env -i PATH=/usr/bin:/bin VAN_GOGH_STATE_DIR=<tmp> sh setup.sh --prepare-only`: rc 0, private CPython 3.12.15 venv, requests+msal import. Side effect again: it registered com.monet.setup-nudge in the real gui domain (plist under the temp HOME); evaluator booted it out |
| P9 | PASS | test_ledger_measures_each_class_and_the_step_set, test_no_secret_value_reaches_ledger_state_stamp_or_logs pass |
| P10 | PASS | test_fanout.py + test_week_review_scan.py run 20 times: 20 of 20 green (70 tests each) |
| P11 | PASS | New test takes a fresh 14-day scan and a 60-day scan through a dated fake Graph, reuses the 60-day snapshot for a 14-day briefing with every client failing on contact: ids, buckets_md, front_page_md equal; 60-day snapshot asserted wider. Mutation: removing `window_sections` in a copy's _run_reuse_scan turns the test red |
| P12 | PASS | test_claude_cli_limits, staged cap test, degraded report exits nonzero, other-provider text not the cap, unknown stays unknown: all pass |
| P13 | NEEDS_HUMAN | Owner, inside P1: zero com.monet jobs before, expected set after, each kick-started job exits 0 |
| P14 | PASS | `tools/preflight.py` live rc 1 naming the exact fault (google_consent_screen 'unattested'; Microsoft consumers + organizations ok). Bogus app id via copied credentials: rc 1, consumers `unauthorized_client`. Approval-link scopes (test) == MS_SCOPES == scopes in a real MSAL authorization URL minus reserved (4 Graph scopes). Note: the organizations probe reported "accepts work or school accounts" for the bogus id too, so that probe cannot tell a missing app from a real one |
| P15 | NEEDS_HUMAN | Newest public run is `publish 0.80.2`; no 0.81.0 run. test.yml parses (jobs pytest, plugin-validate; windows-latest) and carries check.ps1, setup.ps1 -PrepareOnly under powershell and pwsh, e2e step. Nobel: publish, read the windows job log; owner Windows timed install |
| P16 | PASS | scan_docs_truth --selftest fires all 6 rules; live: 43 docs incl. templates/pre-call-checklist.md at the repo root, 0 stale claims; checklist no longer states a time; README has no install-time claim. Note: install-van-gogh/SKILL.md:91 "keep the install inside fifteen minutes" is a model instruction, not matched by TIME_RE |
| P17 | PASS | Full suite 3536 passed, 4 skipped, 0 failed; baseline ids re-collected from `git archive 85b11d5` (3114) identical to the stored list; `comm -23` prints 0 |
| P18 | PASS | scan_dashes: 21994 added lines in 113 files, 0 hits; independent count over `git diff 85b11d5 HEAD` = 0; selftest finds both plants. Publish tree: 409 files, 5 values, planted_found true, 0 hits (`--selftest` flag does not exist; the plant runs every invocation) |
| P19 | FAIL | Nightly half PASS: golden regenerated from `git show 85b11d5:app/meeting_ingest.py` via write_golden, `cmp` identical. Literal half fails: tests/test_scheduler_golden.py:340-354 changed since the pin (Windows _SETTINGS, -Principal). Ruling: if Nobel rules P32's Windows fields supersede "scheduler golden tests unchanged", P19 passes |
| P20 | FAIL | Fresh `claude -p --tools ""` agent caught both plants. Real render + checklist NOT clean, each verified in the text: (1) nudge line "9 of 9 steps left. Next: paste the setup line again in Terminal" and render lines 46-47: the setup line is given nowhere (checklist carries only {{CHECK_MAC}}/{{CHECK_WINDOWS}}, tools/precall.py has no setup line); (2) templates/pre-call-checklist.md:96-97 "a short reminder email ... from your own account to your own address ... Reply to it": setup_nudge._send mails account->same account, a reply reaches only the client; (3) schedule option "Choose times later" described "All four on"; (4) priorities options "A plausible starting point; edit later." x3 (setup_flow.py:1226); (5) "three boxes" closing says jobs not scheduled twice in a row |
| P21 | PASS | auto-pick by local clock 11:59/12:01 America/New_York incl UTC-aware, first-day Afternoon Tea open list non-empty: pass |
| P22 | PASS | test_check_scripts.py pass. Live `sh check.sh` with van-gogh-key-test.json: 8 ok lines, READY, rc 0; without a key: "MISSING: No key file found...", NOT READY, rc 1 |
| P23 | NEEDS_HUMAN | Owner: seed-quality read of run 1, both briefings on screen in the recording |
| P24 | PASS | lifecycle, degraded-keeps-checklist, artifact-deleted-reads-open tests pass; box keys == ss.KEYS |
| P25 | NEEDS_HUMAN | Evaluator half PASS: all 35 skills except install reference _shared/python-runtime.md whose "The setup line" section runs `setup_state.py line` (install prints it via `next`); hook tests pass (3 folders under 1 s, silent cases). Live half: Nobel's run 2 interrupted at step 3, line visible in a new session in another folder, resume finishes |
| P26 | NEEDS_HUMAN | Evaluator half PASS (cadence, week-asleep once, restart, no secrets, went-quiet, job gone, no-sign-in notification). Live: one real nudge in Nobel's inbox with its message id in the ledger + checklist-removed control |
| P27 | PASS | test_three_meetings_are_really_filed_into_the_vault: real meeting_ingest prefetch + staged filing write 3 pages with the 3 expected titles; 0-meeting and sign-in cases and human-box nudge pass |
| P28 | PASS | test_setup_adopt.py: stuck = jobs only open, complete = stamp + 0 nudges, credential write/keep/replace, update path invokes adopt |
| P29 | PASS | Fixtures now carry capture provenance; evaluator reproduced both live: token endpoint bogus-code POST returns exactly the 7 keys/types claimed (AADSTS9002313), Google prompt=none returns 302 `?state=&error_subtype=access_denied&error=interaction_required`. Values changed, structure kept. Behaviour tests (<=120 chars with resume command, launch counts, mismatch, ledger row, unverified-app class) pass |
| P30 | PASS | n/a, skipped, unskippable, cap at 3 steps stays pending and resumes without redo, closing names skipped boxes: pass |
| P31 | FAIL | Live half fixed: config_loader.py:56 calls live_check_quietly, test proves one refresh a day and a failed refresh reopens accounts and nudges. Uninstall test now asserts stamp, checklist and nudge job exist first. Still failing: "a LOCAL one on every command" does not exist. setup_flow.py:2104-2124 returns before any check when the last live check is under 24 h old, and the only other verify_stamp(live=False) callers are cmd_prepare (:677) and run_next (:1585). An unloaded job is found by an ordinary command at most once a day, not on every command |
| P32 | NEEDS_HUMAN | Evaluator half PASS (test_windows_task_fields_field_by_field). Owner: standard user on fresh Windows, trigger one task, recording, 0 prompts, no console flash |
| P33 | PASS | Holder `setup_flow.py _keepawake` now spans the install (started by the first `next`, ends at finish/reset/error, 45 min cap). Live: holder started `caffeinate -i -w <holder pid>`; kill -9 holder -> caffeinate gone within 0.5 s. Windows SetThreadExecutionState held in the holder process (stubbed test). Bell >30 s and clock-time tests pass |
| P34 | PASS | 0o600 file in 0o700 private folder, gone from Downloads, kept after completion and stall; check.sh and discover/adopt search the private folder |
Overall: FAIL (4 of 26 gradeable items failing: P2, P19, P20, P31; 8 NEEDS_HUMAN)
Side effects: the P8 live run registered com.monet.setup-nudge into the real gui launchd domain again (booted out; 3 original jobs remain). Harness fast arm re-run once (real tokens, model calls); no other arm run concurrently.

### Evaluation 2026-10-01 23:24, round 3
Contract hash: verified (464d4100...682e34f recomputed = stored). Graded at HEAD 38715c0, tree clean. Every item re-graded from scratch.
| Item | Verdict | Evidence |
|---|---|---|
| P1 | NEEDS_HUMAN | evidence/run-1/ and run-2/ do not exist. Nobel: 2 timed runs on a fresh macOS user; `setup_flow.py report --ledger evidence/run-N/timing.jsonl` exits 0; recording; both controls |
| P2 | FAIL | Evaluator RE-RAN `--arm fast` twice at 38715c0 (tree clean, one at a time): 10:43 and 6:46, both over 6:00. Floors held (487/488 threads, 15 deals, 10 of 10 filed), isolation under temp, real .env/config.json sha256 unchanged (checked independently). Critical path is one meeting summary: run A summary_timings max 624.0 s (11 model calls for 10 meetings: meeting_synthesis.synthesize timeout=600 hit, then retried), run B max 354.2 s (10 calls, one long meeting). `--compare` vs base 8:27 and vs serial-new 7:26 both print COMPARE FAIL on time. Ledger on file (harness-fast.json, 3:11 at 251a49b) is not reproduced. Page clause: content mode still differs on 10 of 11 pages; structure mode 0 differing. Ruling would change only the page clause; timing fails either way |
| P3 | PASS | test_happy_path_reaches_done_with_exactly_five_prompts: prompts == PROMPT_IDS (5), 2 sign-in calls, "2 signed in" |
| P4 | PASS | test_v2_key_from_the_tool_is_accepted_by_both_parsers, v1, corrupt, two-file discover, no-key create-your-own tests pass |
| P5 | PASS | README.md:304 line (no `&&`) in `env -i zsh -f`, empty HOME, URL swapped for a local git smart-HTTP mirror of HEAD, `--prepare-only`: rc 0, "Plugin installed", `claude plugin list` shows van-gogh@van-gogh 0.81.0 enabled |
| P6 | PASS | test_killed_at_a_step_boundary_the_rerun_finishes_the_same, 10 params over sf.STEPS, all pass |
| P7 | PASS | stamped case 0 steps; stamp-removed rerun 0 new pages, 0 duplicate jobs |
| P8 | PASS | scan_no_sudo.py live 81 files PASS; plant in a copy's app/warm_start.py found (HIT sudo :986). Independent grep of setup.sh/ps1, check.*, app/*.py, hooks: comments only. Live `env -i PATH=/usr/bin:/bin VAN_GOGH_STATE_DIR=<tmp> sh setup.sh --prepare-only`: rc 0, private CPython 3.12.15 venv, requests+msal import. No job registered this time |
| P9 | PASS | test_ledger_measures_each_class_and_the_step_set, test_no_secret_value_reaches_ledger_state_stamp_or_logs pass |
| P10 | PASS | test_fanout.py + test_week_review_scan.py 20 runs: 20 of 20 green (70 tests each) |
| P11 | PASS | test_a_sixty_day_scan_reused_for_a_14_day_briefing_lists_the_fresh_14_day_ids passes; test and app code unchanged since round 2 mutation check |
| P12 | PASS | cap tests pass; fixture 2026-09-09-claude-usage-cap.txt now equals the real log line verbatim; Granola text not the cap; unknown stays unknown; degraded report exits 1 |
| P13 | NEEDS_HUMAN | Owner, inside P1 |
| P14 | PASS | preflight live: Microsoft consumers + organizations ok, rc 1 naming google_consent_screen 'unattested'. Bogus id: consumers fault audience, organizations fault not_found (AADSTS50059), control_rejected true. precall.admin_consent_url scopes == real MSAL get_authorization_request_url scopes minus reserved (4 Graph scopes) |
| P15 | NEEDS_HUMAN | No 0.81.0 public run (`gh run list` newest publish 0.80.2). test.yml parses (pytest matrix incl windows-latest), check.ps1, setup.ps1 -PrepareOnly under powershell and pwsh, e2e step present |
| P16 | PASS | scan_docs_truth selftest fires 6 rules; live 43 docs incl repo-root checklist, 0 stale; README and checklist state no install time |
| P17 | PASS | full suite 3617 passed, 4 skipped, 0 failed; base ids re-collected from `git archive 85b11d5` (3114) identical to stored list; `comm -23` = 0 |
| P18 | PASS | scan_dashes: 24069 added lines, 116 files, 0 hits; independent count 0 em, 0 en; selftest finds both plants. scan_publish_tree: 411 files, 5 values, planted_found true, 0 hits |
| P19 | FAIL | Nightly half PASS: golden regenerated via ingest_fixture.write_golden from 85b11d5's meeting_ingest.py, byte-identical to tests/snapshots/nightly_ingest_golden.json. Literal half fails: tests/test_scheduler_golden.py:340-354 changed since the pin (Windows _SETTINGS, -Principal). Ruling: if P32's Windows fields supersede "scheduler golden tests unchanged", P19 passes |
| P20 | FAIL | Fresh `claude -p --tools ""` reader caught both plants (SERIOUS: 2). Real render + checklist: 1 serious. Checklist item 1 (templates/pre-call-checklist.md, rendered lines 17-25): "Install Claude from https://claude.com/download ... At the plain prompt type `claude`". Verified: /Applications/Claude.app ships no `claude` CLI on PATH (this Mac's is the Homebrew npm package), and check.sh:22 itself says "Install Claude Code from claude.com/code". A client who follows item 1 has no `claude` command for the setup line or the readiness check |
| P21 | PASS | auto-pick 11:59/12:01 America/New_York incl UTC-aware 15:59Z/16:01Z; first-day Afternoon Tea open list non-empty |
| P22 | PASS | test_check_scripts.py pass (each item removed, renamed, private folder). Live check.sh with van-gogh-key-test.json: 8 ok, READY, rc 0; without: "MISSING: No key file found", NOT READY, rc 1 |
| P23 | NEEDS_HUMAN | Owner: seed-quality read of run 1, both briefings on screen |
| P24 | PASS | lifecycle, degraded-keeps-checklist, artifact-deleted-reads-open tests pass |
| P25 | NEEDS_HUMAN | Evaluator half PASS: 34 of 35 skills reference _shared/python-runtime.md "The setup line" (install prints it via `next`); hook tests pass (3 folders under 1 s, silent cases). Live half: Nobel's run 2 interrupted at step 3, line in a new session elsewhere, resume finishes |
| P26 | NEEDS_HUMAN | Evaluator half PASS (cadence 1/3/7, week-asleep once, restart, no secrets, went-quiet, job gone, no-sign-in notification). Live: real nudge in Nobel's inbox with message id in ledger + control |
| P27 | PASS | test_three_meetings_are_really_filed_into_the_vault (3 real pages, expected titles), 0-meeting and sign-in cases pass |
| P28 | PASS | test_setup_adopt.py stuck/complete/credential/update-path tests pass |
| P29 | PASS | fixtures carry capture provenance (structure kept, values invented); behaviour tests pass |
| P30 | PASS | n/a, skipped, unskippable, cap at 3 steps pending and resumes, closing names skipped boxes |
| P31 | FAIL | Local check now runs on every command (config_loader.py tail -> stamp_check_quietly -> verify_stamp(live=True, quiet_checks)); tests pass. But with the network off on a day the live check is due, every command reopens the install: evaluator test (temp copy of tests, `_run_script` raising OSError "network is unreachable", clock +25 h) got `already_installed False, reopened ['accounts'], line "1 of 9 install steps left. Next: sign in to your accounts"` and the nudge job installed. Cause: setup_flow.py:2367-2373 `_refresh_works` returns False on any OSError/nonzero rc, so offline reads as revoked. Also the local check has no token expiry check (contract: "token file present, expiry, job registered"); ss._check_accounts only tests the key exists |
| P32 | NEEDS_HUMAN | Evaluator half PASS (test_windows_task_fields_field_by_field). Owner: standard user on fresh Windows, trigger a task, recording |
| P33 | PASS | Live: `setup_flow.py _keepawake` with a temp checklist spawned `caffeinate -i -w <holder pid>`; kill -9 holder, caffeinate gone within 1.5 s. Windows holder test, bell >30 s, clock-time closing tests pass |
| P34 | PASS | 0o600 key in 0o700 private folder, gone from Downloads, kept after completion and stall; check.sh and discover/adopt search the private folder |
Overall: FAIL (4 of 27 gradeable items failing: P2, P19, P20, P31; 7 NEEDS_HUMAN)
Side effects: none left. P5 and P8 live runs used temp HOME/state dirs; `launchctl list` afterwards shows only com.monet.job-watch, ingest-workspace, meeting-ingest. Harness fast arm re-run twice at HEAD, sequentially, never with another arm.

### Evaluation 2026-10-02 00:25, round 4
Contract hash: verified (464d4100...682e34f recomputed = stored). Graded at HEAD 7fe1151, tree clean. Every item re-graded from scratch.
| Item | Verdict | Evidence |
|---|---|---|
| P1 | NEEDS_HUMAN | evidence/run-1/ and run-2/ do not exist. Nobel: 2 timed runs on a fresh macOS user; `setup_flow.py report --ledger evidence/run-N/timing.jsonl` exits 0; recording; both controls |
| P2 | FAIL | Time PASS: evaluator RE-RAN `--arm fast` twice at 7fe1151 (code_sha in ledger, tree clean, one at a time): 3:09 and 3:08 (505/504 threads, 15 deals, 10 of 10 filed, max summary call 40.7 s and 45.9 s), ratio 0.37 vs base 8:27 and 0.42 vs serial-new 7:26; isolation probe under temp; real .env and config.json sha256 unchanged (checked independently before and after). Budget mutation (no per-call cap, no min-remaining gate) in a copy turns 4 tests in test_meeting_ingest_budget.py red. Serial arm graded from the ledger on file, not re-run. Page clause fails as written: `--compare harness-serial-base.json <fast>` prints `page compare mode: content ... differing 10 ... COMPARE FAIL` for both runs; serial-new vs fast: 1 of 11 content hashes equal; structure mode 0 differing, COMPARE PASS. Ruling: if Nobel accepts structure identity as "normalized content", P2 passes |
| P3 | PASS | test_happy_path_reaches_done_with_exactly_five_prompts passes (5 prompts, sign-in reached, accounts >= 1) |
| P4 | PASS | test_v2_key_from_the_tool_is_accepted_by_both_parsers, two-file, v1, corrupt, no-key create-your-own tests pass |
| P5 | PASS | README.md:304 line (0 `&&`), run in `env -i zsh -f` with an empty HOME (clean Claude config), URL swapped for a local smart-HTTP git mirror of 7fe1151's plugin tree, `--prepare-only`: rc 0, "Plugin installed", `claude plugin list` shows van-gogh@van-gogh 0.81.0 enabled |
| P6 | PASS | test_killed_at_a_step_boundary_the_rerun_finishes_the_same, parametrized over sf.STEPS, all pass |
| P7 | PASS | stamped case 0 steps; stamp-removed rerun 0 new pages, 0 duplicate jobs; both pass |
| P8 | PASS | scan_no_sudo.py live 81 files PASS; plant `subprocess.run(["sudo","true"])` in a copy's app/setup_nudge.py found (HIT :659, FAIL). Live `env -i HOME=<tmp> PATH=/usr/bin:/bin VAN_GOGH_STATE_DIR=<tmp> sh setup.sh --prepare-only`: rc 0, uv-built CPython 3.12.15 venv, requests+msal import. No job registered |
| P9 | PASS | test_ledger_measures_each_class_and_the_step_set, test_no_secret_value_reaches_ledger_state_stamp_or_logs pass |
| P10 | PASS | test_fanout.py + test_week_review_scan.py run 20 times: 20 of 20 green (70 tests each) |
| P11 | PASS | test_a_sixty_day_scan_reused_for_a_14_day_briefing_lists_the_fresh_14_day_ids passes |
| P12 | PASS | test_a_cap_files_what_finished_and_defers_the_rest_with_a_count, cap-classifier and degraded-report tests pass |
| P13 | NEEDS_HUMAN | Owner, inside P1 |
| P14 | PASS | preflight live rc 1: Microsoft consumers + organizations ok (control_rejected true), fault named google_consent_screen 'unattested'. Bogus app id via a copied credentials file: rc 1, consumers fault audience, organizations fault not_found. precall.admin_consent_url scopes == scopes in a real MSAL get_authorization_request_url (MS_SCOPES, the list auth_bootstrap consents with) minus reserved: 4 Graph scopes, non-empty |
| P15 | NEEDS_HUMAN | `gh run list -R NVentures/project-van-gogh-claude`: newest is publish 0.80.2, no 0.81.0 run. test.yml parses (pytest matrix incl windows-latest), check.ps1 cases, setup.ps1 -PrepareOnly under powershell and pwsh, e2e step present |
| P16 | PASS | scan_docs_truth selftest fires 6 rules; live 43 docs incl repo-root templates/pre-call-checklist.md, 0 stale; README and checklist state no install time |
| P17 | PASS | full suite 3686 passed, 4 skipped (baseline 4), 0 failed; base ids re-collected from `git archive 85b11d5` (3114) identical to the stored list; `comm -23` vs current 3690 ids = 0 |
| P18 | PASS | scan_dashes: 26202 added lines in 120 files, 0 hits; independent count 0 em, 0 en; selftest finds both plants. scan_publish_tree: 412 files, 5 values, planted_found true, 0 hits |
| P19 | FAIL | Nightly half PASS: evaluator ran 85b11d5's meeting_ingest.py and HEAD's through ingest_fixture.run_nightly under the primed config: both == golden, regenerated file `cmp` byte-identical to tests/snapshots/nightly_ingest_golden.json. Literal half fails: tests/test_scheduler_golden.py:340-354 changed since the pin (Windows _SETTINGS battery flags, -Hidden, -Principal). Ruling: if P32's Windows fields supersede "scheduler golden tests unchanged", P19 passes |
| P20 | FAIL | Fresh `claude -p --tools ""` reader caught both plants (SERIOUS: 2). Real render + checklist: SERIOUS 0, but not none: 1 contradiction verified in the files. Checklist templates/pre-call-checklist.md:194-195 "You cannot click in that window: move through a list with the arrow keys and press Enter" vs installer "Tap" in setup_flow.py:138 "or tap Yes, continue", :143 and :145 "Tap Change something", :975 "4. Tap \"I signed in\"". Also flagged minor: "choose the note access scopes" (notetaker_granola.py:30) never says which |
| P21 | PASS | auto-pick 11:59/12:01 America/New_York incl UTC-aware; first-day Afternoon Tea open list non-empty |
| P22 | PASS | test_check_scripts.py pass. Live check.sh with van-gogh-key-test.json: 8 ok, READY, rc 0; without: "MISSING: No key file found", NOT READY, rc 1; HOME with ~/.local/bin/claude and PATH=/usr/bin:/bin: "MISSING: Claude is installed but this window cannot find it yet" with the PATH line, NOT READY |
| P23 | NEEDS_HUMAN | Owner: seed-quality read of run 1, both briefings on screen |
| P24 | PASS | lifecycle, degraded-keeps-checklist, artifact-deleted-reads-open tests pass |
| P25 | NEEDS_HUMAN | Evaluator half PASS: 34 of 35 skills reference _shared/python-runtime.md "The setup line" (install prints it via `next`); test_every_command_in_the_manifest_reaches_the_setup_line and hook tests pass. Live half: Nobel's run 2 interrupted at step 3, line in a new session elsewhere, resume finishes |
| P26 | NEEDS_HUMAN | Evaluator half PASS (test_setup_nudge.py). Live: one real nudge in Nobel's inbox with message id in ledger + checklist-removed control |
| P27 | PASS | test_three_meetings_are_really_filed_into_the_vault (3 real pages, expected titles), 0-meeting and sign-in cases pass |
| P28 | PASS | test_setup_adopt.py stuck/complete/credential/update-path tests pass |
| P29 | PASS | fixtures unchanged since round 3, carry capture provenance (structure kept, values invented); test_signin_failures.py behaviour tests pass |
| P30 | PASS | n/a, skipped, unskippable, cap at 3 steps pending and resumes, closing names skipped boxes |
| P31 | PASS | Round-3 defect fixed: refresh_state is three-valued (setup_flow.py:2433-2461), offline = UNKNOWN, nothing reopened, live stamp not advanced. Live probe with a temp state dir and a dead proxy: Microsoft refresh -> `unknown` in 0.2 s. test_offline_with_the_live_check_due_is_not_told_to_sign_in_again, test_an_unloaded_job_reopens_the_box_the_same_day_with_the_network_off (<5 s), test_an_expired_sign_in_reopens_accounts_on_the_local_check_alone (expiry stamp written by auth_bootstrap.py:716/894), test_reset_removes_checklist_stamp_and_nudge_job_together (all three asserted first) pass |
| P32 | NEEDS_HUMAN | Evaluator half PASS (test_windows_task_fields_field_by_field). Owner: standard user on fresh Windows, trigger a task, recording |
| P33 | PASS | Live: `setup_flow.py _keepawake` with a temp checklist spawned `caffeinate -i -w <holder pid>`; kill -9 holder, caffeinate gone in 0.06 s. Windows holder, bell >30 s, clock-time closing tests pass |
| P34 | PASS | 0o600 key in 0o700 private folder, gone from Downloads, kept after completion and stall; check.sh and discover/adopt search the private folder |
Overall: FAIL (3 of 27 gradeable items failing: P2, P19, P20; 7 NEEDS_HUMAN)
Side effects: none left. Harness fast arm re-run twice at HEAD, sequentially, never with another arm. P5 and P8 used temp HOME and state dirs; `launchctl list` afterwards shows only com.monet.job-watch, ingest-workspace, meeting-ingest. Local git server stopped.

### Evaluation 2026-10-05 11:24, round 5
Contract hash: verified (97c188a3...b26d2 recomputed = stored). Contract diff 7fe1151..HEAD touches only P2 and P19 text plus the header note. Graded at HEAD f2b7e8e, tree clean at start. Every item re-graded from scratch.
| Item | Verdict | Evidence |
|---|---|---|
| P1 | NEEDS_HUMAN | evidence/run-1/ and run-2/ do not exist. Nobel: 2 timed runs on a fresh macOS user; `setup_flow.py report --ledger evidence/run-N/timing.jsonl` exits 0; recording; both controls |
| P2 | PASS | `--selftest` raises on a config pointing home. Evaluator RE-RAN `--arm fast` (code_sha f2b7e8e): 3:22, 398 threads, 15 deals, 10 of 10 filed, max summary call 64.8 s; isolation line under temp. Serial ledger on file no longer matched the page set (3 pages each side differ: the ten newest meetings drifted since 10-01), so the serial arm was RE-RUN after the fast arm finished: 16:42, 389 threads, 15 deals, 10 filed. `--compare serial-r5 fast-r5`: ratio 0.20, page compare mode structure, 11 shared, 0 differing, COMPARE PASS. Against base control 8:27: ratio 0.40, time passes (page clause there is content mode, not the amended clause). Real .env and config.json sha256 identical before and after both arms |
| P3 | PASS | test_happy_path_reaches_done_with_exactly_five_prompts passes |
| P4 | PASS | test_v2_key_from_the_tool_is_accepted_by_both_parsers, test_v1_key_still_parses_as_version_one, test_discover_lists_both_downloads_copies_newest_first, test_a_corrupt_key_file_writes_nothing_and_stays_put, test_discover_with_no_key_file_returns_the_create_your_own_text pass |
| P5 | PASS | README.md:304 line (0 `&&`) in `env -i HOME=<empty> zsh -f`, URL swapped for a local smart-HTTP git mirror of f2b7e8e's plugin tree, `--prepare-only`: rc 0, "Plugin installed", `claude plugin list` shows van-gogh@van-gogh 0.81.0 enabled |
| P6 | PASS | test_killed_at_a_step_boundary_the_rerun_finishes_the_same, 10 params, all pass |
| P7 | PASS | test_stamp_present_means_already_installed_and_zero_steps and test_stamp_removed_with_everything_on_disk_reruns_with_zero_new pass |
| P8 | PASS | tools/scan_no_sudo.py live 81 files PASS; no --selftest flag, so plants in tree copies: `subprocess.run(["sudo","true"])` in app/meeting_ingest.py found (HIT :2419), `brew install python` in setup.sh found (HIT :172). Live `env -i HOME=<tmp> PATH=/usr/bin:/bin VAN_GOGH_STATE_DIR=<tmp> sh setup.sh --prepare-only` (python3 3.9.6, no uv): rc 0, private CPython 3.12.15 venv, requests+msal import |
| P9 | PASS | test_ledger_measures_each_class_and_the_step_set, test_no_secret_value_reaches_ledger_state_stamp_or_logs pass |
| P10 | PASS | test_fanout.py + test_week_review_scan.py run 20 times: 20 of 20 green (70 tests each); failed lookup is an error line, retry branch asserted (lookups == 2) |
| P11 | PASS | test_a_sixty_day_scan_reused_for_a_14_day_briefing_lists_the_fresh_14_day_ids passes |
| P12 | PASS | test_a_cap_files_what_finished_and_defers_the_rest_with_a_count, test_another_providers_usage_limit_is_not_the_cap, unknown-stays-unknown and degraded-report tests pass |
| P13 | NEEDS_HUMAN | Owner, inside P1 |
| P14 | PASS | preflight live: Microsoft consumers + organizations ok (control_rejected true), names the fault google_consent_screen 'unattested'. Bogus app id via a copied credentials file: rc 1, consumers fault audience, organizations fault not_found (AADSTS50059). precall.admin_consent_url scopes == scopes in a real MSAL get_authorization_request_url minus reserved: 4 Graph scopes, non-empty |
| P15 | NEEDS_HUMAN | `gh run list -R NVentures/project-van-gogh-claude`: newest is publish 0.80.2, no 0.81.0 run. test.yml parses (pytest matrix incl windows-latest), check.ps1 cases, setup.ps1 -PrepareOnly under powershell and pwsh, e2e step present |
| P16 | PASS | scan_docs_truth selftest fires 6 rules; live 43 docs incl repo-root templates/pre-call-checklist.md, 0 stale; independent grep of README and checklist: only "Every 15 minutes" job cadence, no install time |
| P17 | PASS | full suite 3686 passed, 4 skipped (baseline 4), 0 failed; base ids re-collected from `git archive 85b11d5` (3114) identical to the stored list; `comm -23` vs current 3690 ids = 0 |
| P18 | PASS | scan_dashes: 26263 added lines in 120 files, 0 hits; selftest finds both plants; independent count over `git diff 85b11d5 HEAD` (whole repo) 0 em, 0 en. scan_publish_tree: 412 files, 5 values, planted_found true, 0 hits |
| P19 | PASS | Nightly: evaluator ran the ENTIRE 85b11d5 app tree (its own conftest priming, its config_loader and meeting_synthesis, asserted by path) and HEAD's through ingest_fixture.write_golden: both `cmp` byte-identical to tests/snapshots/nightly_ingest_golden.json (10 files, note_a1 in stdout). Scheduler golden: `git diff 85b11d5 HEAD -- tests/test_scheduler_golden.py` changes only _SETTINGS (two battery flags, -Hidden, Interactive Limited principal) and `-Principal $principal`, all P32 fields, as the amended text allows; test passes |
| P20 | PASS | Control: fresh `claude -p --tools ""` reader on the real render with the two plants inserted (no header telling it plants exist): SERIOUS 2, both plants caught. Real render + checklist, two fresh readers: contradictions none, none SERIOUS; each raised 1 MINOR, both checked against the product and neither is real: (a) "choose the row named Other" not in the catalogue: AskUserQuestion always adds its own Other row (skills/install-van-gogh/SKILL.md:43-45); (b) where the notetaker key is pasted: the skill asks for it as a plain reply (SKILL.md:63-65). Both come from the catalogue not rendering on-screen elements |
| P21 | PASS | test_auto_picks_the_briefing_by_the_local_clock (11:59/12:01 local and UTC-aware) and test_afternoon_tea_builds_its_open_list_from_the_scan_on_the_first_day pass |
| P22 | PASS | test_check_scripts.py pass. Live check.sh from a folder with van-gogh-key-test.json: 8 ok, READY, rc 0; empty HOME, no key: "MISSING: Claude is installed but not signed in", "MISSING: No key file found", NOT READY, rc 1 |
| P23 | NEEDS_HUMAN | Owner: seed-quality read of run 1, both briefings on screen |
| P24 | PASS | test_checklist_and_key_file_lifecycle, test_degraded_run_keeps_the_checklist_open_boxes_named_no_stamp_key_file_kept, test_an_artifact_deleted_mid_run_reads_open_again pass |
| P25 | NEEDS_HUMAN | Evaluator half PASS: test_every_command_in_the_manifest_reaches_the_setup_line and test_setup_notice_hook.py pass. Live half: Nobel's run 2 interrupted at step 3, line in a new session elsewhere, resume finishes |
| P26 | NEEDS_HUMAN | Evaluator half PASS (test_setup_nudge.py: 1/3/7, week asleep once, day-5 restart, no secret, gone-checklist row, no-sign-in notification). Live: one real nudge in Nobel's inbox with message id in ledger + checklist-removed control |
| P27 | PASS | test_three_meetings_are_really_filed_into_the_vault, 0-meeting and sign-in cases, human-box nudge tests pass |
| P28 | PASS | test_stuck_install_adopts_with_exactly_the_jobs_box_open, test_complete_install_adopts_silently..., credential keep/replace, test_the_update_path_invokes_adopt pass |
| P29 | PASS | 5 fixtures under tests/fixtures/signin carry CAPTURED provenance, unchanged since 38715c0; test_signin_failures.py passes |
| P30 | PASS | not-applicable, skipped, unskippable, cap at 3 steps pending and resumed, closing names skipped boxes: pass |
| P31 | PASS | test_offline_with_the_live_check_due_is_not_told_to_sign_in_again, test_an_unloaded_job_reopens_the_box_the_same_day_with_the_network_off, test_an_expired_sign_in_reopens_accounts_on_the_local_check_alone, test_verify_stamp_local_is_fast_and_says_already_installed_with_the_network_off, test_reset_removes_checklist_stamp_and_nudge_job_together pass |
| P32 | NEEDS_HUMAN | Evaluator half PASS (test_windows_task_fields_field_by_field). Owner: standard user on fresh Windows, trigger a task, recording, 0 prompts, no console flash |
| P33 | PASS | Live: `setup_flow.py _keepawake` with a temp checklist spawned `caffeinate -i -w <holder pid>`; kill -9 holder, caffeinate gone in 0.05 s. Windows holder, bell over 30 s, clock-time closing tests pass |
| P34 | PASS | test_install_all_writes_credentials_ingests_companion_and_moves_the_file, ..._from_the_private_dir_is_a_resume_not_a_move, discover searches the private dir; check.sh private-folder test passes |
Overall: PASS (27 of 27 gradeable items PASS; 7 NEEDS_HUMAN: P1, P13, P15, P23, P25 live half, P26 live half, P32 owner half)
Side effects: none left by the evaluator. Fast arm then serial arm, sequentially (real tokens, model calls). P5 and P8 used temp HOME and state dirs; `launchctl list` afterwards shows only com.monet.job-watch, ingest-workspace, meeting-ingest; local git server stopped. Not the evaluator's: untracked `project-van-gogh/assets/slack-app-manifest.json` appeared at 11:16 during grading (nothing at HEAD writes it); left in place.

## After round 4 (not graded), and round 5 on the amended contract

Round 4 was the fourth and last evaluation loop: 24 of 27 gradeable items PASS, 3 FAIL (P2, P19, P20), 7 NEEDS_HUMAN. What happened next, none of it re-graded by the evaluator:

- P20's one finding (the checklist says "you cannot click in that window" while six installer lines said "tap") was fixed in commit after round 4: the lines now say "choose" and "one answer". Suite 3686 passed, 4 skipped; scanners pass. The builder's own four stranger's reads before round 4 had gone from 8 contradictions to 0 serious findings; this one was introduced by the last checklist edit and not re-read.
- P2 and P19 fail only on the literal text and wait on Nobel's ruling (see the receipt). P2's time clause passes (evaluator re-runs: 3:09 and 3:08 against the 8:27 base). No code change can satisfy P2's page clause as written, because two live runs never produce the same model-written prose; P19's scheduler half conflicts with P32, which requires the Windows task fields that changed that golden file.
- 2026-10-05: Nobel ruled (P2 page clause = page identity; P32 overrides P19). The contract was amended on those two items, re-stamped (hash 97c188a3...), and round 5 graded every item again from scratch at f2b7e8e: 27 of 27 gradeable PASS, 7 NEEDS_HUMAN (P1, P13, P15, P23, P25, P26, P32). The evaluator's fresh ledgers are filed as `evidence/harness-fast-evaluator-round5.json` (3:22) and `evidence/harness-serial-new-evaluator-round5.json` (16:42, the same ten meetings, page identity 11 of 11).

## Live end-to-end run before the merge (2026-10-10 to 2026-10-11, not graded by the evaluator)

Nobel said merge once it is sure to work, so the builder drove the orchestrator end to end against his real accounts in isolated folders (a temp state dir seeded with his tokens, a temp vault, the key file built by `tools/keyfile.py` in the working folder, the branch's plugin loaded with `--plugin-dir` and the installed copy excluded). A driver played the skill's part: it called `next`, answered the prompts with real values, waited when told, and ran the two briefing agents headlessly. The run reached `done` with every box resolved, the checklist deleted, the stamp written, 10 meetings filed, 3 deals tracked, and two real briefings written (machine time 5:11; the wall clock is meaningless because the run was paused for fixes). It found six defects the stubbed tests could not see, all fixed with tests before the merge:

1. A resumed or adopted install (tokens on disk, no config yet) wrote the template's placeholder accounts into the config; the scan then crashed on a token that does not exist. The vault step now derives accounts from the tokens and refuses to write a placeholder.
2. The detached scan sent its output nowhere; a dead scan was undiagnosable. It now logs, and `wait-scan` reports the reason.
3. The scan declared itself ready before the file the deals step reads existed. The write order and the readiness check are fixed.
4. The deals witness used `verify`'s re-proposal count, which can never reach zero for a subset of picks. It now checks the picks themselves (`verify --picked`).
5. `apply` silently dropped a ticked deal as a duplicate of another deal in the same batch on a shared place word. A pick is never dropped against its own batch; skips are reported.
6. A resume across midnight could not find the proposal staged the day before. `apply` and `verify` find the newest, and picks are re-mapped by heading after a re-proposal.
7. The briefings witness demanded the hot cache heading verbatim; the skills render deals by counterparty and subject. It now accepts a distinctive word of the heading or the counterparty.

Also seen: the research job started without the confirmed company (fixed); the jobs box was ticked from this Mac's real launchd (the checker reads the machine, which is right on a client's machine and will not happen on a fresh user account).

## Lessons

- The control for "half of today" must be the shipped code measured on a clean checkout of the pin, before the freeze. The plan's 25 to 40 minute budget came from the old skill's prose; the base measured 8:27. Recorded in lab-notes (PROOF ARTIFACTS, SPEED-CONTRACT CONTROL) and observation 94.
- A wall-clock bar over live model calls is a sample unless every unit of work has a ceiling. One of ten summary calls took 139 to 624 s in five runs and was footnoted as noise until the evaluator's re-run failed on it; the fix was a budget with a cheaper fallback and a queued full read. Recorded in lab-notes (UNBOUNDED UNIT) and observation 96.
- A cold read converges over rounds and checks consistency, never truth. It failed in all four evaluator rounds on different lines; the builder ran no read of its own before round 1, and three wording reads passed a checklist that named the wrong download. Recorded in lab-notes (COLD READ CHECKS CONSISTENCY, NOT TRUTH) and observation 95.
- Two frozen items contradicted each other (P19 "scheduler golden tests unchanged" against P32's required Windows fields) and one asked for something no live run can produce (P2 "same pages by normalized content" for model-written text). Neither red team asked "can two items both be true" or "can a live run satisfy this twice". Recorded as observation 97.
- Version and base commit must be bound at branch time through one pin, not typed into the contract (observation 93); a notifier must not depend on the step it reports on (observation 92); a long plan needs a rendering designed for the reviewer (observation 91).
- A harness run under an overridden state dir registered a real scheduler job twice during evaluation. Anything that installs into the OS scheduler must refuse when the state dir is overridden; fixed in `setup_flow._nudge_job`.

## Red-team changes

Red team ran on the 21-item draft. It changed 19 verify steps (floors and
non-empty-input guards on every check, controls that must fail for the named
reason only, evaluation-time state named per item) and added P22 to P24.
"Evaluator" items run with no human. "Owner" items need Nobel at the keyboard;
the evaluator grades the artifacts they leave.


Added 2026-10-01 after the gap hunt. A second red team ran on these ten and
sharpened every one (exact counts instead of "not twice", real captured
provider payloads as fixtures, silent cases asserting exit 0 and empty
stderr, positive and negative cases in the same suite). Its biggest finding,
that the nudge job could not reach the stuck client, changed section B2.


Receipt and proof: `~/Downloads/proof-fifteen-minute-install/`.
